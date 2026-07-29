'use client'
import { Suspense, useState, useEffect, useCallback, useRef } from 'react'
import Link from 'next/link'
import { useSearchParams } from 'next/navigation'
import {
  listSuppliers, createSupplier, updateSupplier, deleteSupplier,
} from '@/lib/api'
import type { Supplier } from '@/lib/types'
import Spinner from '@/components/ui/Spinner'
import { EmptyState, ErrorState, InlineError, LoadingState, SkeletonTable } from '@/components/ui/States'
import { useLanguage } from '@/contexts/LanguageContext'
import { DEFAULT_LEAD_TIME_DAYS } from '@/lib/inventoryDefaults'
import { useConfirm } from '@/components/ui/ConfirmDialog'
import Card from '@/components/ui/Card'
import Input, { Field, Select, Textarea } from '@/components/ui/Input'
import Table, { Th, Td } from '@/components/ui/Table'
import Tooltip from '@/components/ui/Tooltip'
import PriceBreakManager from '@/components/suppliers/PriceBreakManager'
import {
  Truck, Plus, Edit2, Trash2, Save, Info, ChevronDown, ChevronRight, BarChart3, Tag,
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
  payment_terms:  string
  notes:          string
}

function blankForm(name = ''): SupplierForm {
  return { name, email: '', phone: '', whatsapp: '', lead_time_days: String(DEFAULT_LEAD_TIME_DAYS), lead_time_std: '3', payment_terms: '', notes: '' }
}

function supplierToForm(s: Supplier): SupplierForm {
  return {
    name:           s.name,
    email:          s.email ?? '',
    phone:          s.phone ?? '',
    whatsapp:       s.whatsapp ?? '',
    lead_time_days: String(s.lead_time_days),
    lead_time_std:  String(s.lead_time_std),
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
  const [form, setForm] = useState<SupplierForm>(initial ? supplierToForm(initial) : blankForm(prefillName))
  const set = (k: keyof SupplierForm) => (e: React.ChangeEvent<HTMLInputElement | HTMLSelectElement | HTMLTextAreaElement>) =>
    setForm(f => ({ ...f, [k]: e.target.value }))

  const canSave = form.name.trim().length > 0

  return (
    <Card tone="inset" padding="20px 24px" style={{ display: 'flex', flexDirection: 'column', gap: 16 }}>
      <div style={{ fontSize: 14, fontWeight: 700, color: C.text }}>
        {initial ? t('suppliers.form_edit_title') : t('suppliers.form_new_title')}
      </div>

      {/* Name */}
      <Field label={`${t('suppliers.form_name_label')} *`} labelStyle={FORM_LABEL_STYLE}>
        <Input name="supplier_name" aria-label={t('suppliers.form_name_label')} placeholder={t('suppliers.form_name_placeholder')} value={form.name} onChange={set('name')} autoFocus />
      </Field>

      {/* Email + Phone */}
      <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 12 }}>
        <Field label={t('suppliers.form_email_label')} labelStyle={FORM_LABEL_STYLE}>
          <Input name="supplier_email" aria-label={t('suppliers.form_email_label')} type="email" placeholder={t('suppliers.form_email_placeholder')} value={form.email} onChange={set('email')} />
        </Field>
        <Field label={t('suppliers.form_phone_label')} labelStyle={FORM_LABEL_STYLE}>
          <Input name="supplier_phone" aria-label={t('suppliers.form_phone_label')} placeholder="+506 8888 8888" value={form.phone} onChange={set('phone')} />
        </Field>
      </div>

      {/* WhatsApp + Payment Terms */}
      <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 12 }}>
        <Field label={t('suppliers.form_whatsapp_label')} labelStyle={FORM_LABEL_STYLE}>
          <Input name="supplier_whatsapp" aria-label={t('suppliers.form_whatsapp_label')} placeholder="+506 8888 8888" value={form.whatsapp} onChange={set('whatsapp')} />
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
      <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 12 }}>
        {/* The hint below this field states the precedence explicitly (the lesson
            from the event multipliers): this value governs every SKU of the
            supplier that has no lead time of its own — a distributor has 12
            suppliers, not 2.000 lead times — and a SKU set by hand keeps its own. */}
        <Field
          label={
            <Tooltip text={t('suppliers.form_lead_time_tip')}>
              <span>{t('suppliers.form_lead_time_label')} *</span>
              <Info size={9} color={C.dim} style={{ opacity: 0.5 }} aria-hidden="true" />
            </Tooltip>
          }
          labelStyle={FORM_LABEL_STYLE}
          hint={t('suppliers.lead_time_applies_to_catalog')}
          hintStyle={{ fontSize: 10, lineHeight: 1.5 }}
        >
          <Input name="supplier_lead_time_days" type="number" min={1} max={365} value={form.lead_time_days} onChange={set('lead_time_days')} aria-label={t('suppliers.form_lead_time_label')} />
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

      {/* Actions */}
      <div style={{ display: 'flex', gap: 8, justifyContent: 'flex-end' }}>
        <button
          onClick={onCancel}
          style={{ all: 'unset', cursor: 'pointer', padding: '7px 14px', borderRadius: 7, border: `1px solid ${C.border}`, color: C.dim, fontSize: 13 }}
        >
          {t('common.cancel')}
        </button>
        <button
          onClick={() => canSave && onSave(form)}
          disabled={!canSave || saving}
          style={{
            all: 'unset', cursor: canSave && !saving ? 'pointer' : 'default',
            display: 'flex', alignItems: 'center', gap: 6,
            padding: '7px 16px', borderRadius: 7, fontSize: 13, fontWeight: 600,
            background: C.indigo, color: '#fff', opacity: canSave && !saving ? 1 : 0.5,
          }}
        >
          {saving ? <Spinner size={12} /> : <Save size={12} aria-hidden="true" />}
          {initial ? t('suppliers.form_submit_update') : t('suppliers.form_submit_create')}
        </button>
      </div>
    </Card>
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
  // Typed with the learning counters the list endpoint now ships alongside each
  // supplier; they are optional so an older backend simply renders no state.
  const [suppliers, setSuppliers] = useState<SupplierWithLearning[]>([])
  const [loading,   setLoading]   = useState(true)
  // The raw error is kept (not a flattened string) so ErrorState/InlineError
  // can classify it by kind. `loadError` is the one that blanks the screen;
  // `actionError` is a save/delete failure over an already-rendered list.
  const [loadError,   setLoadError]   = useState<unknown>(null)
  const [actionError, setActionError] = useState<string | null>(null)
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
      lead_time_days: parseInt(form.lead_time_days) || DEFAULT_LEAD_TIME_DAYS,
      lead_time_std:  parseInt(form.lead_time_std) || 3,
      payment_terms:  form.payment_terms || null,
      notes:          form.notes.trim() || null,
    }
    try {
      if (editing) {
        await updateSupplier(editing.id, payload)
      } else {
        await createSupplier(payload as Omit<Supplier, 'id' | 'tenant_id' | 'created_at' | 'active'>)
      }
      setShowForm(false); setEditing(null)
      await load()
    } catch (e: unknown) {
      setActionError(e instanceof Error ? e.message : t('suppliers.err_saving'))
    } finally {
      setSaving(false)
    }
  }

  async function handleDelete(id: string) {
    const s = suppliers.find(x => x.id === id)
    if (!(await confirm({
      title: `${t('suppliers.delete_confirm_q')} "${s?.name}"?`,
      message: t('suppliers.delete_confirm_warn'),
      danger: true,
    }))) return
    setActionError(null)
    try { await deleteSupplier(id); await load() }
    catch (e: unknown) { setActionError(e instanceof Error ? e.message : t('suppliers.err_deleting')) }
  }

  function handleEdit(s: Supplier) { setEditing(s); setShowForm(true) }
  function handleCancel() { setShowForm(false); setEditing(null); setPrefillName(undefined) }
  function handleToggleExpand(id: string) { setExpandedId(cur => (cur === id ? null : id)) }

  const isFormOpen = showForm || !!editing

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 20 }}>

      {/* Header */}
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', flexWrap: 'wrap', gap: 10 }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
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
          }}>
            <BarChart3 size={13} aria-hidden="true" /> {t('suppliers.scorecard_link')}
          </Link>
          {!isFormOpen && (
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
                       'Faro learns each supplier’s real lead time from the receptions you record, and replaces the configured value once there is enough evidence.')],
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
