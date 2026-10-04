'use client'
import { Suspense, useState, useEffect, useCallback, useRef } from 'react'
import Link from 'next/link'
import { useSearchParams } from 'next/navigation'
import {
  listSuppliers, createSupplier, updateSupplier, deleteSupplier,
} from '@/lib/api'
import type { Supplier } from '@/lib/types'
import Spinner from '@/components/ui/Spinner'
import { EmptyState, ErrorState, InlineError, LoadingState, SkeletonTable, useErrorDetail } from '@/components/ui/States'
import { useLanguage } from '@/contexts/LanguageContext'
import { DEFAULT_LEAD_TIME_DAYS } from '@/lib/inventoryDefaults'
import { useConfirm } from '@/components/ui/ConfirmDialog'
import Card from '@/components/ui/Card'
import Input, { Field, Select, Textarea } from '@/components/ui/Input'
import Table, { Th, Td } from '@/components/ui/Table'
import Tooltip from '@/components/ui/Tooltip'
import PriceBreakManager from '@/components/suppliers/PriceBreakManager'
import { useIsNarrow } from '@/hooks/useIsNarrow'
import { BottomSheet, MobileList, MobileCard } from '@/components/mobile'
import StickyActionBar from '@/components/mobile/StickyActionBar'
import {
  Truck, Plus, Edit2, Trash2, Save, Info, ChevronDown, ChevronRight, BarChart3, Tag,
  Mail, Phone, MessageCircle,
} from 'lucide-react'

// ── Palette ───────────────────────────────────────────────────────────────────
const C = {
  surface: 'var(--surface)', card: 'var(--surface-2)', border: 'var(--border)',
  text: 'var(--text)', muted: 'var(--muted)', dim: 'var(--dim)',
  green: '#22c55e', amber: '#f59e0b', red: '#ef4444', indigo: 'var(--accent)',
}

// ── Lead-time learning ────────────────────────────────────────────────────────
// `service.resolve_lead_time` replaces the configured lead time with the one
// learned from this supplier's real receptions — but only past
// MIN_LEAD_TIME_OBSERVATIONS of them, a bar a new tenant never clears. So the
// planner quietly used our own assumption and no screen admitted the mechanism
// existed. `GET /inventory/suppliers` now ships the counters (observations
// recorded, observations needed, learned average) so this row can state where
// the learning stands. The threshold comes from the payload, never from a
// literal here: a number that can disagree with the planner is worse than none.
interface SupplierLearning {
  lead_time_observations?:        number
  lead_time_observations_needed?: number
  lead_time_learned_days?:        number | null
  lead_time_learned_unusable?:    boolean
}
type SupplierWithLearning = Supplier & SupplierLearning

/** `t` returns the key itself when the catalog has no entry for it. Rendering
 *  "suppliers.learning_none" at the buyer is worse than plain English — the
 *  guard `lib/explanationCopy.ts` already uses. */
function tOr(
  t: (key: string, params?: Record<string, unknown>) => string,
  key: string, fallback: string, params?: Record<string, unknown>,
): string {
  const text = t(key, params)
  return text === key ? fallback : text
}

function LeadTimeLearning({ supplier }: { supplier: SupplierWithLearning }) {
  const { t } = useLanguage()
  const needed = supplier.lead_time_observations_needed
  // Older backend: say nothing rather than invent a threshold.
  if (needed == null) return null

  const seen = supplier.lead_time_observations ?? 0
  const learned = supplier.lead_time_learned_days

  if (seen >= needed && learned != null) {
    return (
      <span style={{ color: C.green }}>
        {tOr(t, 'suppliers.learning_active',
          `Learned from ${seen} deliveries: ${learned} days on average — that is the number we plan with.`,
          { n: seen, days: learned })}
      </span>
    )
  }
  // Enough deliveries recorded, none of them usable — every one arrived the
  // same day it was ordered, so they say nothing about how long this supplier
  // takes. Saying "N more and we adjust" here would promise what cannot happen.
  if (supplier.lead_time_learned_unusable) {
    return (
      <span style={{ color: C.dim }}>
        {tOr(t, 'suppliers.learning_unusable',
          `Recorded ${seen} deliveries, but each arrived the same day it was ordered, so they say nothing about this supplier's lead time. Still using the ${supplier.lead_time_days} days you configured.`,
          { n: seen, days: supplier.lead_time_days })}
      </span>
    )
  }
  if (seen === 0) {
    return (
      <span style={{ color: C.dim }}>
        {tOr(t, 'suppliers.learning_none',
          `No deliveries from this supplier recorded yet. Once you receive ${needed} orders, we adjust the lead time on our own.`,
          { needed })}
      </span>
    )
  }
  return (
    <span style={{ color: C.dim }}>
      {tOr(t, 'suppliers.learning_partial',
        `${seen} of ${needed} deliveries recorded. ${needed - seen} more and we adjust the lead time on our own.`,
        { n: seen, needed, missing: needed - seen })}
    </span>
  )
}

// Labels inside the supplier form sit below a panel heading, so they read one
// step quieter than the Field default.
const FORM_LABEL_STYLE: React.CSSProperties = { fontSize: 11, color: 'var(--dim)' }

const PAYMENT_TERMS = ['Contado', '15 días', '30 días', '60 días', '90 días', 'Otro']

// ── Empty form state ──────────────────────────────────────────────────────────
interface SupplierForm {
  name:           string
  email:          string
  phone:          string
  whatsapp:       string
  lead_time_days: string
  lead_time_std:  string
  review_period_days: string
  payment_terms:  string
  notes:          string
}

/**
 * A new supplier starts with NO lead time.
 *
 * It used to open pre-filled with the system default, and the form sends what
 * it holds — so `_stamp_lead_time_provenance` recorded SOURCE_USER for every
 * supplier created here, and the scorecard printed **DECLARADO 15d** for a
 * supplier who declared nothing. Three backend call sites gate on
 * `lead_time_set_by` precisely to keep StockAI's own assumption from being
 * reported as the supplier's promise; a pre-filled field defeated all three
 * (stability 11.32).
 *
 * Empty is not missing information: the planner falls back to the same default
 * it always did. What changes is that the product no longer claims somebody
 * chose it.
 */
function blankForm(name = ''): SupplierForm {
  return { name, email: '', phone: '', whatsapp: '', lead_time_days: '', lead_time_std: '3', review_period_days: '', payment_terms: '', notes: '' }
}

function supplierToForm(s: Supplier): SupplierForm {
  return {
    name:           s.name,
    email:          s.email ?? '',
    phone:          s.phone ?? '',
    whatsapp:       s.whatsapp ?? '',
    lead_time_days: String(s.lead_time_days),
    lead_time_std:  String(s.lead_time_std),
    review_period_days: s.review_period_days ? String(s.review_period_days) : '',
    payment_terms:  s.payment_terms ?? '',
    notes:          s.notes ?? '',
  }
}

// ── Form panel ────────────────────────────────────────────────────────────────
function SupplierFormPanel({
  initial,
  prefillName,
  onSave,
  onCancel,
  saving,
}: {
  initial?: Supplier
  prefillName?: string
  onSave: (form: SupplierForm) => void
  onCancel: () => void
  saving: boolean
}) {
  const { t } = useLanguage()
  // Phone: the same form in a bottom sheet, one field per row, the save
  // pinned under it.
  const narrow = useIsNarrow()
  const pair: React.CSSProperties = { display: 'grid', gridTemplateColumns: narrow ? '1fr' : '1fr 1fr', gap: 12 }
  const [form, setForm] = useState<SupplierForm>(initial ? supplierToForm(initial) : blankForm(prefillName))
  const set = (k: keyof SupplierForm) => (e: React.ChangeEvent<HTMLInputElement | HTMLSelectElement | HTMLTextAreaElement>) =>
    setForm(f => ({ ...f, [k]: e.target.value }))

  const canSave = form.name.trim().length > 0

  const title = initial ? t('suppliers.form_edit_title') : t('suppliers.form_new_title')
  const submit = (
    <button
      onClick={() => canSave && onSave(form)}
      disabled={!canSave || saving}
      className={narrow ? 'mobile-btn mobile-btn-primary' : undefined}
      style={narrow ? undefined : {
        all: 'unset', cursor: canSave && !saving ? 'pointer' : 'default',
        display: 'flex', alignItems: 'center', gap: 6,
        padding: '7px 16px', borderRadius: 7, fontSize: 13, fontWeight: 600,
        background: C.indigo, color: '#fff', opacity: canSave && !saving ? 1 : 0.5,
      }}
    >
      {saving ? <Spinner size={12} /> : <Save size={narrow ? 16 : 12} aria-hidden="true" />}
      {initial ? t('suppliers.form_submit_update') : t('suppliers.form_submit_create')}
    </button>
  )
  const fields = (
    <>

      {/* Name */}
      <Field label={`${t('suppliers.form_name_label')} *`} labelStyle={FORM_LABEL_STYLE}>
        <Input name="supplier_name" aria-label={t('suppliers.form_name_label')} placeholder={t('suppliers.form_name_placeholder')} value={form.name} onChange={set('name')} autoFocus />
      </Field>

      {/* Email + Phone */}
      <div style={pair}>
        <Field label={t('suppliers.form_email_label')} labelStyle={FORM_LABEL_STYLE}>
          <Input name="supplier_email" inputMode="email" autoComplete="email" aria-label={t('suppliers.form_email_label')} type="email" placeholder={t('suppliers.form_email_placeholder')} value={form.email} onChange={set('email')} />
        </Field>
        <Field label={t('suppliers.form_phone_label')} labelStyle={FORM_LABEL_STYLE}>
          <Input name="supplier_phone" type="tel" inputMode="tel" aria-label={t('suppliers.form_phone_label')} placeholder="+506 8888 8888" value={form.phone} onChange={set('phone')} />
        </Field>
      </div>

      {/* WhatsApp + Payment Terms */}
      <div style={pair}>
        <Field label={t('suppliers.form_whatsapp_label')} labelStyle={FORM_LABEL_STYLE}>
          <Input name="supplier_whatsapp" type="tel" inputMode="tel" aria-label={t('suppliers.form_whatsapp_label')} placeholder="+506 8888 8888" value={form.whatsapp} onChange={set('whatsapp')} />
        </Field>
        <Field label={t('suppliers.form_payment_terms_label')} labelStyle={FORM_LABEL_STYLE}>
          {/* The chevron is drawn here rather than via `chevron`, because it has
              to sit inside the relative wrapper this layout already uses. */}
          <div style={{ position: 'relative' }}>
            <Select style={{ paddingRight: 28, appearance: 'none' }} name="supplier_payment_terms" value={form.payment_terms} onChange={set('payment_terms')} aria-label={t('suppliers.form_payment_terms_label')}>
              <option value="">{t('suppliers.form_select_placeholder')}</option>
              {PAYMENT_TERMS.map(term => <option key={term} value={term}>{term}</option>)}
            </Select>
            <ChevronDown size={11} style={{ position: 'absolute', right: 9, top: '50%', transform: 'translateY(-50%)', pointerEvents: 'none', color: C.dim }} aria-hidden="true" />
          </div>
        </Field>
      </div>

      {/* Lead time + Variability */}
      <div style={pair}>
        {/* The hint below this field states the precedence explicitly (the lesson
            from the event multipliers): this value governs every SKU of the
            supplier that has no lead time of its own — a distributor has 12
            suppliers, not 2.000 lead times — and a SKU set by hand keeps its own. */}
        <Field
          label={
            <Tooltip text={t('suppliers.form_lead_time_tip')}>
              <span>{t('suppliers.form_lead_time_label')}</span>
              <Info size={9} color={C.dim} style={{ opacity: 0.5 }} aria-hidden="true" />
            </Tooltip>
          }
          labelStyle={FORM_LABEL_STYLE}
          hint={t('suppliers.lead_time_applies_to_catalog')}
          hintStyle={{ fontSize: 10, lineHeight: 1.5 }}
        >
          {/* Placeholder, not a value: it shows what StockAI will assume while
              leaving the field empty, so nothing is recorded as declared. */}
          <Input name="supplier_lead_time_days" type="number" inputMode="numeric" min={1} max={365}
                 value={form.lead_time_days} onChange={set('lead_time_days')}
                 placeholder={t('suppliers.form_lead_time_placeholder', { days: DEFAULT_LEAD_TIME_DAYS })}
                 aria-label={t('suppliers.form_lead_time_label')} />
        </Field>
        <Field
          label={
            <Tooltip text={t('suppliers.form_variability_tip')}>
              <span>{t('suppliers.form_variability_label')}</span>
              <Info size={9} color={C.dim} style={{ opacity: 0.5 }} aria-hidden="true" />
            </Tooltip>
          }
          labelStyle={FORM_LABEL_STYLE}
        >
          <Input name="supplier_lead_time_std" type="number" min={0} max={60} value={form.lead_time_std} onChange={set('lead_time_std')} aria-label={t('suppliers.form_variability_label')} />
        </Field>

        {/* How often this buyer orders from this supplier. The order has to
            cover until the NEXT one arrives, not just until this one does, so
            a weekly cadence on a 10-day lead time is 17 days of protection.
            Empty is 0 — no declared cadence, and the arithmetic stays as it
            was for every tenant that never fills this in. */}
        <Field
          label={
            <Tooltip text={t('suppliers.form_review_period_tip')}>
              <span>{t('suppliers.form_review_period_label')}</span>
              <Info size={9} color={C.dim} style={{ opacity: 0.5 }} aria-hidden="true" />
            </Tooltip>
          }
          labelStyle={FORM_LABEL_STYLE}
        >
          <Input name="supplier_review_period_days" type="number" min={0} max={365}
                 placeholder={t('suppliers.form_review_period_placeholder')}
                 value={form.review_period_days} onChange={set('review_period_days')}
                 aria-label={t('suppliers.form_review_period_label')} />
        </Field>
      </div>

      {/* Notes */}
      <Field label={t('suppliers.form_notes_label')} labelStyle={FORM_LABEL_STYLE}>
        <Textarea
          style={{ minHeight: 64 }}
          name="supplier_notes"
          placeholder={t('suppliers.form_notes_placeholder')}
          value={form.notes}
          onChange={set('notes')}
          aria-label={t('suppliers.form_notes_label')}
        />
      </Field>

    </>
  )

  if (narrow) return (
    <BottomSheet open onClose={onCancel} title={title} maxHeight="92dvh"
      footer={(
        <div style={{ display: 'flex', gap: 8, width: '100%' }}>
          <button type="button" className="mobile-btn mobile-btn-secondary" onClick={onCancel} style={{ flex: '0 0 auto' }}>{t('common.cancel')}</button>
          {submit}
        </div>
      )}>
      <div style={{ display: 'flex', flexDirection: 'column', gap: 16 }}>{fields}</div>
    </BottomSheet>
  )

  return (
    <Card tone="inset" padding="20px 24px" style={{ display: 'flex', flexDirection: 'column', gap: 16 }}>
      <div style={{ fontSize: 14, fontWeight: 700, color: C.text }}>
        {title}
      </div>
      {fields}

      {/* Actions */}
      <div style={{ display: 'flex', gap: 8, justifyContent: 'flex-end' }}>
        <button
          onClick={onCancel}
          style={{ all: 'unset', cursor: 'pointer', padding: '7px 14px', borderRadius: 7, border: `1px solid ${C.border}`, color: C.dim, fontSize: 13 }}
        >
          {t('common.cancel')}
        </button>
        {submit}
      </div>
    </Card>
  )
}

// ── Phone: supplier detail sheet ──────────────────────────────────────────────
function SupplierSheet({ supplier, onClose, onEdit, onDelete }: {
  supplier: SupplierWithLearning | null
  onClose: () => void
  onEdit: (s: Supplier) => void
  onDelete: (id: string) => void
}) {
  const { t } = useLanguage()
  const s = supplier
  const linkS: React.CSSProperties = {
    display: 'flex', alignItems: 'center', gap: 10, minHeight: 44, color: C.indigo,
    textDecoration: 'none', fontSize: 15, overflowWrap: 'anywhere',
  }
  const factLabel: React.CSSProperties = { fontSize: 12, color: C.dim }
  const factValue: React.CSSProperties = { fontSize: 15, fontWeight: 600, color: C.text, marginTop: 2 }
  return (
    <BottomSheet open={!!s} onClose={onClose} title={s?.name ?? ''} maxHeight="92dvh"
      footer={s ? (
        <div style={{ display: 'flex', gap: 8, width: '100%' }}>
          <button type="button" className="mobile-btn mobile-btn-secondary" onClick={() => onDelete(s.id)}
                  aria-label={`${t('suppliers.row_delete')}: ${s.name}`}
                  style={{ flex: '0 0 auto', color: 'var(--signal-order-now-fg)' }}>
            <Trash2 size={16} aria-hidden="true" />
          </button>
          <button type="button" className="mobile-btn mobile-btn-primary" onClick={() => onEdit(s)}>
            <Edit2 size={16} aria-hidden="true" /> {t('suppliers.row_edit')}
          </button>
        </div>
      ) : undefined}>
      {s && (
        <div style={{ display: 'flex', flexDirection: 'column', gap: 16 }}>
          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(2, minmax(0, 1fr))', gap: '14px 12px' }}>
            <div><div style={factLabel}>{t('suppliers.table_lead_time')}</div><div style={{ ...factValue, color: C.indigo }}>{s.lead_time_days}d</div></div>
            <div><div style={factLabel}>{t('suppliers.table_variability')}</div><div style={factValue}>±{s.lead_time_std}d</div></div>
            <div><div style={factLabel}>{t('suppliers.table_payment_terms')}</div><div style={factValue}>{s.payment_terms || '—'}</div></div>
            <div><div style={factLabel}>{t('suppliers.form_review_period_label')}</div><div style={factValue}>{s.review_period_days ? `${s.review_period_days}d` : '—'}</div></div>
          </div>
          <div style={{ fontSize: 13.5, lineHeight: 1.5, padding: '10px 12px', borderRadius: 10, background: C.card, border: `1px solid ${C.border}` }}>
            <LeadTimeLearning supplier={s} />
          </div>
          {(s.email || s.phone || s.whatsapp) && (
            <div>
              <div style={factLabel}>{t('suppliers.table_contact')}</div>
              {s.email && <a href={`mailto:${s.email}`} style={linkS}><Mail size={16} aria-hidden="true" />{s.email}</a>}
              {s.phone && <a href={`tel:${s.phone.replace(/\s+/g, '')}`} style={linkS}><Phone size={16} aria-hidden="true" />{s.phone}</a>}
              {s.whatsapp && <a href={`https://wa.me/${s.whatsapp.replace(/[^0-9]/g, '')}`} target="_blank" rel="noopener noreferrer" style={linkS}><MessageCircle size={16} aria-hidden="true" />{s.whatsapp}</a>}
            </div>
          )}
          {s.notes && <p style={{ margin: 0, fontSize: 13.5, color: C.muted, lineHeight: 1.5 }}>{s.notes}</p>}
          <PriceBreakManager supplier={s} />
        </div>
      )}
    </BottomSheet>
  )
}

// ── Supplier row ──────────────────────────────────────────────────────────────
function SupplierRow({
  supplier,
  onEdit,
  onDelete,
  expanded,
  onToggleExpand,
  first,
}: {
  supplier: SupplierWithLearning
  onEdit: (s: Supplier) => void
  onDelete: (id: string) => void
  expanded: boolean
  onToggleExpand: (id: string) => void
  /** Carries the tour anchors, so each resolves to exactly one cell. */
  first?: boolean
}) {
  const { t } = useLanguage()
  return (
    <>
      <tr
        style={{ borderBottom: expanded ? 'none' : `1px solid ${C.border}` }}
        onMouseEnter={e => (e.currentTarget.style.background = 'color-mix(in srgb, var(--accent) 3%, transparent)')}
        onMouseLeave={e => (e.currentTarget.style.background = 'transparent')}
      >
        {/* The row divider lives on the <tr> here, because an expanded row has
            to suppress it — hence `divider={false}` on every cell. */}
        <Td size="lg" divider={false} style={{ fontSize: 13, fontWeight: 600, color: C.text }}>{supplier.name}</Td>
        <Td size="lg" divider={false} mono data-tour={first ? 'sup.leadtime' : undefined} style={{ color: C.indigo, fontWeight: 700 }}>
          {supplier.lead_time_days}d
        </Td>
        <Td size="lg" divider={false} style={{ color: C.dim }}>
          ±{supplier.lead_time_std}d
        </Td>
        {/* What the lead-time learning is waiting for. A default nobody chose
            stops being silent the moment the screen says when it will stop
            being a default. */}
        <Td size="lg" divider={false} data-tour={first ? 'sup.learning' : undefined} style={{ fontSize: 11, lineHeight: 1.5, minWidth: 230 }}>
          <LeadTimeLearning supplier={supplier} />
        </Td>
        <Td size="lg" divider={false} style={{ color: C.muted }}>
          {supplier.payment_terms || <span style={{ color: C.dim }}>—</span>}
        </Td>
        <Td size="lg" divider={false} data-tour={first ? 'sup.contact' : undefined} style={{ color: C.muted }}>
          {supplier.email
            ? <a href={`mailto:${supplier.email}`} style={{ color: C.indigo, textDecoration: 'none' }}>{supplier.email}</a>
            : <span style={{ color: C.dim }}>—</span>}
        </Td>
        <Td size="lg" divider={false} style={{ color: C.muted }}>
          {supplier.phone || supplier.whatsapp
            ? <>{supplier.phone || ''}{supplier.phone && supplier.whatsapp ? ' / ' : ''}{supplier.whatsapp || ''}</>
            : <span style={{ color: C.dim }}>—</span>}
        </Td>
        <Td size="lg" divider={false}>
          <div style={{ display: 'flex', gap: 4 }}>
            <button
              onClick={() => onToggleExpand(supplier.id)}
              data-tour={first ? 'sup.pricebreaks' : undefined}
              title={t('suppliers.pb_toggle')}
              aria-label={`${t('suppliers.pb_toggle')}: ${supplier.name}`}
              aria-expanded={expanded}
              style={{ all: 'unset', cursor: 'pointer', padding: 5, borderRadius: 5, color: expanded ? C.indigo : C.dim, display: 'flex', alignItems: 'center', gap: 2 }}
              onMouseEnter={e => (e.currentTarget.style.color = C.indigo)}
              onMouseLeave={e => (e.currentTarget.style.color = expanded ? C.indigo : C.dim)}
            >
              {expanded ? <ChevronDown size={12} aria-hidden="true" /> : <ChevronRight size={12} aria-hidden="true" />}
              <Tag size={13} aria-hidden="true" />
            </button>
            <button
              onClick={() => onEdit(supplier)}
              title={t('suppliers.row_edit')}
              aria-label={`${t('suppliers.row_edit')}: ${supplier.name}`}
              style={{ all: 'unset', cursor: 'pointer', padding: 5, borderRadius: 5, color: C.dim, display: 'flex' }}
              onMouseEnter={e => (e.currentTarget.style.color = C.indigo)}
              onMouseLeave={e => (e.currentTarget.style.color = C.dim)}
            >
              <Edit2 size={13} aria-hidden="true" />
            </button>
            <button
              onClick={() => onDelete(supplier.id)}
              title={t('suppliers.row_delete')}
              aria-label={`${t('suppliers.row_delete')}: ${supplier.name}`}
              style={{ all: 'unset', cursor: 'pointer', padding: 5, borderRadius: 5, color: C.dim, display: 'flex' }}
              onMouseEnter={e => (e.currentTarget.style.color = C.red)}
              onMouseLeave={e => (e.currentTarget.style.color = C.dim)}
            >
              <Trash2 size={13} aria-hidden="true" />
            </button>
          </div>
        </Td>
      </tr>
      {expanded && (
        <tr style={{ borderBottom: `1px solid ${C.border}` }}>
          <td colSpan={8} style={{ padding: '0 16px 14px' }}>
            <PriceBreakManager supplier={supplier} />
          </td>
        </tr>
      )}
    </>
  )
}

// ── Main page ─────────────────────────────────────────────────────────────────
function SuppliersPageInner() {
  const searchParams = useSearchParams()
  // One-click path from /hoy's "missing contact info" banner: ?focus=<name>
  // opens that supplier's edit form directly, or pre-fills a new one if it
  // doesn't exist yet under that name.
  const focusName = searchParams.get('focus')
  const [prefillName, setPrefillName] = useState<string | undefined>(undefined)
  const focusHandled = useRef(false)

  const { t } = useLanguage()
  const confirm = useConfirm()
  // Phone: cards + a detail sheet instead of the 8-column table, and the add
  // action pinned above the tab bar.
  const narrow = useIsNarrow()
  const [detailId, setDetailId] = useState<string | null>(null)
  // Typed with the learning counters the list endpoint now ships alongside each
  // supplier; they are optional so an older backend simply renders no state.
  const [suppliers, setSuppliers] = useState<SupplierWithLearning[]>([])
  const [loading,   setLoading]   = useState(true)
  // The raw error is kept (not a flattened string) so ErrorState/InlineError
  // can classify it by kind. `loadError` is the one that blanks the screen;
  // `actionError` is a save/delete failure over an already-rendered list.
  const [loadError,   setLoadError]   = useState<unknown>(null)
  const [actionError, setActionError] = useState<string | null>(null)
  // The banner used to print `e.message`, which for an AppError is the ENGLISH
  // fallback the backend ships for clients that have no catalogue. This screen
  // has one. The global toast was already rendering `errors.<code>` in the
  // user's language, so the same failure read Spanish in the corner and English
  // in the panel — and the panel is the one attached to the form you are
  // looking at. `useErrorDetail` is the shared resolver both now use.
  const errorDetail = useErrorDetail()
  const [saving,    setSaving]    = useState(false)
  const [showForm,  setShowForm]  = useState(false)
  const [editing,   setEditing]   = useState<Supplier | null>(null)
  const [expandedId, setExpandedId] = useState<string | null>(null)

  // `silent: true` — the failure is rendered inline as a full ErrorState, so the
  // interceptor's toast would duplicate it.
  const load = useCallback(async () => {
    setLoading(true); setLoadError(null)
    try { setSuppliers(await listSuppliers({ silent: true })) }
    catch (e: unknown) { setLoadError(e) }
    finally { setLoading(false) }
  }, [])

  useEffect(() => { load() }, [load])

  useEffect(() => {
    if (loading || !focusName || focusHandled.current) return
    focusHandled.current = true
    const match = suppliers.find(s => s.name.toLowerCase() === focusName.toLowerCase())
    if (match) {
      setEditing(match)
      setShowForm(true)
    } else {
      setPrefillName(focusName)
      setEditing(null)
      setShowForm(true)
    }
  }, [loading, focusName, suppliers])

  async function handleSave(form: SupplierForm) {
    setSaving(true); setActionError(null)
    const payload = {
      name:           form.name.trim(),
      email:          form.email.trim() || null,
      phone:          form.phone.trim() || null,
      whatsapp:       form.whatsapp.trim() || null,
      // Omitted when the field is empty: sending a number is what makes the
      // backend stamp it as the supplier's own declaration (11.32).
      lead_time_days: form.lead_time_days.trim() === ''
        ? null : (parseInt(form.lead_time_days) || DEFAULT_LEAD_TIME_DAYS),
      // `|| 3` turned a typed 0 — a supplier who always delivers on the day —
      // into 3 days of spread, and the safety stock grew by z * demand * 3 for
      // a variability the buyer had just said does not exist (math audit
      // 2026-10-01). Only a blank or unreadable box falls back.
      lead_time_std:  Number.isNaN(parseInt(form.lead_time_std)) ? 3 : parseInt(form.lead_time_std),
      // 0 means "no declared cadence" and reproduces the old arithmetic
      // exactly, so an empty box must send 0 rather than nothing.
      review_period_days: parseInt(form.review_period_days) || 0,
      payment_terms:  form.payment_terms || null,
      notes:          form.notes.trim() || null,
    }
    try {
      if (editing) {
        await updateSupplier(editing.id, payload)
      } else {
        await createSupplier(payload)
      }
      setShowForm(false); setEditing(null)
      await load()
    } catch (e: unknown) {
      setActionError(errorDetail(e) || t('suppliers.err_saving'))
    } finally {
      setSaving(false)
    }
  }

  async function handleDelete(id: string) {
    const s = suppliers.find(x => x.id === id)
    // One sheet at a time on a phone: the confirmation replaces the detail.
    setDetailId(null)
    if (!(await confirm({
      title: `${t('suppliers.delete_confirm_q')} "${s?.name}"?`,
      message: t('suppliers.delete_confirm_warn'),
      danger: true,
    }))) return
    setActionError(null)
    try { await deleteSupplier(id); await load() }
    catch (e: unknown) { setActionError(errorDetail(e) || t('suppliers.err_deleting')) }
  }

  function handleEdit(s: Supplier) { setDetailId(null); setEditing(s); setShowForm(true) }
  function handleCancel() { setShowForm(false); setEditing(null); setPrefillName(undefined) }
  function handleToggleExpand(id: string) { setExpandedId(cur => (cur === id ? null : id)) }

  const isFormOpen = showForm || !!editing

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 20 }}>

      {/* Header */}
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', flexWrap: 'wrap', gap: 10 }}>
        <div style={{ display: narrow ? 'none' : 'flex', alignItems: 'center', gap: 10 }}>
          <div style={{
            width: 36, height: 36, borderRadius: 9,
            background: 'linear-gradient(135deg, var(--accent), var(--accent))',
            display: 'flex', alignItems: 'center', justifyContent: 'center',
          }}>
            <Truck size={17} color="#fff" strokeWidth={2.5} aria-hidden="true" />
          </div>
          <div>
            <h1 style={{ margin: 0, fontSize: 16, fontWeight: 700, color: C.text, letterSpacing: '-0.02em' }}>
              {t('suppliers.page_title')}
            </h1>
            <p style={{ margin: 0, fontSize: 11, color: C.dim }}>
              {t('suppliers.page_subtitle')}
            </p>
          </div>
        </div>

        <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
          <Link href="/proveedores/scorecard" data-tour="sup.scorecard" style={{
            display: 'flex', alignItems: 'center', gap: 6,
            fontSize: 12, color: C.dim, textDecoration: 'none',
            padding: '7px 12px', border: `1px solid ${C.border}`, borderRadius: 8,
            ...(narrow ? { minHeight: 44, boxSizing: 'border-box', fontSize: 14, borderRadius: 10 } : {}),
          }}>
            <BarChart3 size={13} aria-hidden="true" /> {t('suppliers.scorecard_link')}
          </Link>
          {/* Hidden while the list is empty: the empty state below carries the
              same "add" button, and the phone already works this way. */}
          {!isFormOpen && !narrow && !(suppliers.length === 0 && !loading && !loadError) && (
            <button
              data-tour="sup.add"
              onClick={() => { setEditing(null); setShowForm(true) }}
              style={{
                all: 'unset', cursor: 'pointer', display: 'flex', alignItems: 'center', gap: 6,
                padding: '8px 16px', borderRadius: 8, fontSize: 13, fontWeight: 600,
                background: C.indigo, color: '#fff',
              }}
            >
              <Plus size={14} aria-hidden="true" /> {t('suppliers.add_supplier')}
            </button>
          )}
        </div>
      </div>

      {/* Save / delete failure over an already-loaded list. */}
      {actionError && (
        <InlineError error={new Error(actionError)} onDismiss={() => setActionError(null)} />
      )}

      {/* Form panel (add / edit) */}
      {isFormOpen && (
        <SupplierFormPanel
          initial={editing ?? undefined}
          prefillName={editing ? undefined : prefillName}
          onSave={handleSave}
          onCancel={handleCancel}
          saving={saving}
        />
      )}

      {/* Content */}
      {loading ? (
        <Card padding={8}>
          <LoadingState label={t('suppliers.loading_label')}>
            <SkeletonTable rows={5} columns={4} />
          </LoadingState>
        </Card>
      ) : loadError ? (
        <ErrorState error={loadError} onRetry={load} />
      ) : suppliers.length === 0 && !isFormOpen ? (
        /* ── Empty state: names the payoff, then opens the form ──── */
        /* The wrapper carries the tour's "add" anchor while the header button
           is hidden, so that step still lands on the button that opens the form. */
        <div data-tour="sup.add">
        <EmptyState
          icon={<Truck size={22} />}
          title={t('suppliers.empty_title')}
          body={t('suppliers.empty_body')}
          bullets={[
            t('suppliers.empty_bullet_1'),
            t('suppliers.empty_bullet_2'),
            t('suppliers.empty_bullet_3'),
          ]}
          actions={[{
            label: t('suppliers.empty_cta'),
            icon: <Plus size={14} />,
            onClick: () => { setEditing(null); setShowForm(true) },
          }]}
        />
        </div>
      ) : suppliers.length > 0 && narrow ? (
        /* ── Phone: one card per supplier, details in a sheet ─────── */
        <MobileList ariaLabel={t('suppliers.page_title')}>
          {suppliers.map(s => (
            <MobileCard key={s.id}
              title={s.name}
              subtitle={[s.payment_terms, s.email || s.phone || s.whatsapp].filter(Boolean).join(' · ') || undefined}
              value={<span style={{ color: C.indigo }}>{s.lead_time_days}d</span>}
              valueCaption={`±${s.lead_time_std}d`}
              onClick={() => setDetailId(s.id)}
            />
          ))}
        </MobileList>
      ) : suppliers.length > 0 ? (
        /* ── Table ───────────────────────────────────────────────── */
        <Card padding={0} overflow="hidden">
          <Table size="lg">
              <thead>
                <tr style={{ background: C.card }}>
                  {[
                    [t('suppliers.table_name'), ''],
                    [t('suppliers.table_lead_time'), t('suppliers.table_lead_time_tip')],
                    [t('suppliers.table_variability'), t('suppliers.table_variability_tip')],
                    [tOr(t, 'suppliers.table_learning', 'Learning'),
                     tOr(t, 'suppliers.table_learning_tip',
                       'StockAI learns each supplier’s real lead time from the receptions you record, and replaces the configured value once there is enough evidence.')],
                    [t('suppliers.table_payment_terms'), ''],
                    [t('suppliers.table_email'), ''],
                    [t('suppliers.table_contact'), ''],
                    [t('suppliers.table_actions'), ''],
                  ].map(([label, tip]) => (
                    <Th key={label} size="lg">
                      {tip ? (
                        <Tooltip text={tip}>
                          <span>{label}</span>
                          <Info size={9} color={C.dim} style={{ opacity: 0.5 }} />
                        </Tooltip>
                      ) : label}
                    </Th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {suppliers.map((s, idx) => (
                  <SupplierRow
                    key={s.id}
                    supplier={s}
                    first={idx === 0}
                    onEdit={handleEdit}
                    onDelete={handleDelete}
                    expanded={expandedId === s.id}
                    onToggleExpand={handleToggleExpand}
                  />
                ))}
              </tbody>
          </Table>
        </Card>
      ) : null}

      {narrow && (
        <SupplierSheet
          supplier={detailId && !isFormOpen ? suppliers.find(s => s.id === detailId) ?? null : null}
          onClose={() => setDetailId(null)}
          onEdit={handleEdit}
          onDelete={handleDelete}
        />
      )}
      {narrow && !loading && !loadError && suppliers.length > 0 && (
        <StickyActionBar hidden={isFormOpen}>
          <button type="button" data-tour="sup.add" className="mobile-btn mobile-btn-primary"
                  onClick={() => { setEditing(null); setShowForm(true) }}>
            <Plus size={18} aria-hidden="true" /> {t('suppliers.add_supplier')}
          </button>
        </StickyActionBar>
      )}

    </div>
  )
}

export default function SuppliersPage() {
  return (
    <Suspense fallback={
      <div style={{ padding: 48, display: 'flex', justifyContent: 'center' }}>
        <Spinner />
      </div>
    }>
      <SuppliersPageInner />
    </Suspense>
  )
}
