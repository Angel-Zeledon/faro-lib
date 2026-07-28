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
import PriceBreakManager from '@/components/suppliers/PriceBreakManager'
import {
  Truck, Plus, Edit2, Trash2, Save, Info, ChevronDown, ChevronRight, BarChart3, Tag,
} from 'lucide-react'

// ── Palette ───────────────────────────────────────────────────────────────────
const C = {
  surface: 'var(--surface)', card: 'var(--surface-2)', border: 'var(--border)',
  text: 'var(--text)', muted: 'var(--muted)', dim: 'var(--dim)',
  green: '#22c55e', amber: '#f59e0b', red: '#ef4444', indigo: '#818cf8',
}

// ── Tooltip ───────────────────────────────────────────────────────────────────
function Tooltip({ text, children }: { text: string; children: React.ReactNode }) {
  const [show, setShow] = useState(false)
  return (
    <span
      style={{ position: 'relative', display: 'inline-flex', alignItems: 'center', gap: 4 }}
      onMouseEnter={() => setShow(true)}
      onMouseLeave={() => setShow(false)}
    >
      {children}
      {show && (
        <span style={{
          position: 'absolute', bottom: 'calc(100% + 6px)', left: '50%', transform: 'translateX(-50%)',
          background: '#1e293b', color: '#e2e8f0', fontSize: 11, lineHeight: 1.55,
          padding: '8px 11px', borderRadius: 7, width: 240, zIndex: 200,
          border: '1px solid #334155', boxShadow: '0 6px 18px rgba(0,0,0,0.5)',
          pointerEvents: 'none', whiteSpace: 'normal',
        }}>
          {text}
          <span style={{
            position: 'absolute', top: '100%', left: '50%', transform: 'translateX(-50%)',
            borderLeft: '5px solid transparent', borderRight: '5px solid transparent',
            borderTop: '5px solid #1e293b',
          }} />
        </span>
      )}
    </span>
  )
}

// ── Shared input style ────────────────────────────────────────────────────────
const inputS: React.CSSProperties = {
  background: 'var(--surface-2)', border: `1px solid var(--border)`,
  borderRadius: 6, color: 'var(--text)', fontSize: 13, outline: 'none',
  padding: '7px 10px', width: '100%', boxSizing: 'border-box',
}

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
    <div style={{
      background: C.card, border: `1px solid ${C.border}`, borderRadius: 10,
      padding: '20px 24px', display: 'flex', flexDirection: 'column', gap: 16,
    }}>
      <div style={{ fontSize: 14, fontWeight: 700, color: C.text }}>
        {initial ? t('suppliers.form_edit_title') : t('suppliers.form_new_title')}
      </div>

      {/* Name */}
      <div>
        <label style={{ fontSize: 11, fontWeight: 600, color: C.dim, display: 'block', marginBottom: 4 }}>
          {t('suppliers.form_name_label')} *
        </label>
        <input style={inputS} name="supplier_name" aria-label={t('suppliers.form_name_label')} placeholder={t('suppliers.form_name_placeholder')} value={form.name} onChange={set('name')} autoFocus />
      </div>

      {/* Email + Phone */}
      <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 12 }}>
        <div>
          <label style={{ fontSize: 11, fontWeight: 600, color: C.dim, display: 'block', marginBottom: 4 }}>
            {t('suppliers.form_email_label')}
          </label>
          <input style={inputS} name="supplier_email" aria-label={t('suppliers.form_email_label')} type="email" placeholder={t('suppliers.form_email_placeholder')} value={form.email} onChange={set('email')} />
        </div>
        <div>
          <label style={{ fontSize: 11, fontWeight: 600, color: C.dim, display: 'block', marginBottom: 4 }}>
            {t('suppliers.form_phone_label')}
          </label>
          <input style={inputS} name="supplier_phone" aria-label={t('suppliers.form_phone_label')} placeholder="+506 8888 8888" value={form.phone} onChange={set('phone')} />
        </div>
      </div>

      {/* WhatsApp + Payment Terms */}
      <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 12 }}>
        <div>
          <label style={{ fontSize: 11, fontWeight: 600, color: C.dim, display: 'block', marginBottom: 4 }}>
            {t('suppliers.form_whatsapp_label')}
          </label>
          <input style={inputS} name="supplier_whatsapp" aria-label={t('suppliers.form_whatsapp_label')} placeholder="+506 8888 8888" value={form.whatsapp} onChange={set('whatsapp')} />
        </div>
        <div>
          <label style={{ fontSize: 11, fontWeight: 600, color: C.dim, display: 'block', marginBottom: 4 }}>
            {t('suppliers.form_payment_terms_label')}
          </label>
          <div style={{ position: 'relative' }}>
            <select style={{ ...inputS, paddingRight: 28, appearance: 'none' as const }} name="supplier_payment_terms" value={form.payment_terms} onChange={set('payment_terms')} aria-label={t('suppliers.form_payment_terms_label')}>
              <option value="">{t('suppliers.form_select_placeholder')}</option>
              {PAYMENT_TERMS.map(term => <option key={term} value={term}>{term}</option>)}
            </select>
            <ChevronDown size={11} style={{ position: 'absolute', right: 9, top: '50%', transform: 'translateY(-50%)', pointerEvents: 'none', color: C.dim }} aria-hidden="true" />
          </div>
        </div>
      </div>

      {/* Lead time + Variability */}
      <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 12 }}>
        <div>
          <label style={{ fontSize: 11, fontWeight: 600, color: C.dim, display: 'block', marginBottom: 4 }}>
            <Tooltip text={t('suppliers.form_lead_time_tip')}>
              <span>{t('suppliers.form_lead_time_label')} *</span>
              <Info size={9} color={C.dim} style={{ opacity: 0.5 }} aria-hidden="true" />
            </Tooltip>
          </label>
          <input style={inputS} name="supplier_lead_time_days" type="number" min={1} max={365} value={form.lead_time_days} onChange={set('lead_time_days')} aria-label={t('suppliers.form_lead_time_label')} />
          {/* This field now governs every SKU of this supplier that has no lead
              time of its own — a distributor has 12 suppliers, not 2.000 lead
              times. State the precedence explicitly (the lesson from the event
              multipliers): the buyer must be able to tell why a given SKU got
              the number it got, and that a SKU they set by hand keeps theirs. */}
          <div style={{ fontSize: 10, color: C.dim, marginTop: 4, lineHeight: 1.5 }}>
            {t('suppliers.lead_time_applies_to_catalog')}
          </div>
        </div>
        <div>
          <label style={{ fontSize: 11, fontWeight: 600, color: C.dim, display: 'block', marginBottom: 4 }}>
            <Tooltip text={t('suppliers.form_variability_tip')}>
              <span>{t('suppliers.form_variability_label')}</span>
              <Info size={9} color={C.dim} style={{ opacity: 0.5 }} aria-hidden="true" />
            </Tooltip>
          </label>
          <input style={inputS} name="supplier_lead_time_std" type="number" min={0} max={60} value={form.lead_time_std} onChange={set('lead_time_std')} aria-label={t('suppliers.form_variability_label')} />
        </div>
      </div>

      {/* Notes */}
      <div>
        <label style={{ fontSize: 11, fontWeight: 600, color: C.dim, display: 'block', marginBottom: 4 }}>
          {t('suppliers.form_notes_label')}
        </label>
        <textarea
          style={{ ...inputS, resize: 'vertical', minHeight: 64 }}
          name="supplier_notes"
          placeholder={t('suppliers.form_notes_placeholder')}
          value={form.notes}
          onChange={set('notes')}
          aria-label={t('suppliers.form_notes_label')}
        />
      </div>

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
    </div>
  )
}

// ── Supplier row ──────────────────────────────────────────────────────────────
function SupplierRow({
  supplier,
  onEdit,
  onDelete,
  expanded,
  onToggleExpand,
}: {
  supplier: Supplier
  onEdit: (s: Supplier) => void
  onDelete: (id: string) => void
  expanded: boolean
  onToggleExpand: (id: string) => void
}) {
  const { t } = useLanguage()
  return (
    <>
      <tr
        style={{ borderBottom: expanded ? 'none' : `1px solid ${C.border}` }}
        onMouseEnter={e => (e.currentTarget.style.background = 'rgba(129,140,248,0.03)')}
        onMouseLeave={e => (e.currentTarget.style.background = 'transparent')}
      >
        <td style={{ padding: '12px 16px', fontWeight: 600, fontSize: 13, color: C.text }}>{supplier.name}</td>
        <td style={{ padding: '12px 16px', fontFamily: 'monospace', fontSize: 12, color: C.indigo, fontWeight: 700 }}>
          {supplier.lead_time_days}d
        </td>
        <td style={{ padding: '12px 16px', fontSize: 12, color: C.dim }}>
          ±{supplier.lead_time_std}d
        </td>
        <td style={{ padding: '12px 16px', fontSize: 12, color: C.muted }}>
          {supplier.payment_terms || <span style={{ color: C.dim }}>—</span>}
        </td>
        <td style={{ padding: '12px 16px', fontSize: 12, color: C.muted }}>
          {supplier.email
            ? <a href={`mailto:${supplier.email}`} style={{ color: C.indigo, textDecoration: 'none' }}>{supplier.email}</a>
            : <span style={{ color: C.dim }}>—</span>}
        </td>
        <td style={{ padding: '12px 16px', fontSize: 12, color: C.muted }}>
          {supplier.phone || supplier.whatsapp
            ? <>{supplier.phone || ''}{supplier.phone && supplier.whatsapp ? ' / ' : ''}{supplier.whatsapp || ''}</>
            : <span style={{ color: C.dim }}>—</span>}
        </td>
        <td style={{ padding: '12px 16px' }}>
          <div style={{ display: 'flex', gap: 4 }}>
            <button
              onClick={() => onToggleExpand(supplier.id)}
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
        </td>
      </tr>
      {expanded && (
        <tr style={{ borderBottom: `1px solid ${C.border}` }}>
          <td colSpan={7} style={{ padding: '0 16px 14px' }}>
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
  const [suppliers, setSuppliers] = useState<Supplier[]>([])
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
    <div style={{ display: 'flex', flexDirection: 'column', gap: 20, animation: 'fadeIn 0.3s ease-out' }}>

      {/* Header */}
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', flexWrap: 'wrap', gap: 10 }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
          <div style={{
            width: 36, height: 36, borderRadius: 9,
            background: 'linear-gradient(135deg, #818cf8, #6366f1)',
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
          <Link href="/inventory/suppliers/scorecard" style={{
            display: 'flex', alignItems: 'center', gap: 6,
            fontSize: 12, color: C.dim, textDecoration: 'none',
            padding: '7px 12px', border: `1px solid ${C.border}`, borderRadius: 8,
          }}>
            <BarChart3 size={13} aria-hidden="true" /> {t('suppliers.scorecard_link')}
          </Link>
          {!isFormOpen && (
            <button
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
        <div style={{ background: C.surface, border: `1px solid ${C.border}`, borderRadius: 12, padding: 8 }}>
          <LoadingState label={t('suppliers.loading_label')}>
            <SkeletonTable rows={5} columns={4} />
          </LoadingState>
        </div>
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
        <div style={{ background: C.surface, border: `1px solid ${C.border}`, borderRadius: 12, overflow: 'hidden' }}>
          <div style={{ overflowX: 'auto' }}>
            <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 12 }}>
              <thead>
                <tr style={{ background: C.card }}>
                  {[
                    [t('suppliers.table_name'), ''],
                    [t('suppliers.table_lead_time'), t('suppliers.table_lead_time_tip')],
                    [t('suppliers.table_variability'), t('suppliers.table_variability_tip')],
                    [t('suppliers.table_payment_terms'), ''],
                    [t('suppliers.table_email'), ''],
                    [t('suppliers.table_contact'), ''],
                    [t('suppliers.table_actions'), ''],
                  ].map(([label, tip]) => (
                    <th
                      key={label}
                      style={{
                        padding: '9px 16px', textAlign: 'left', whiteSpace: 'nowrap',
                        color: C.dim, fontWeight: 600, fontSize: 10,
                        borderBottom: `1px solid ${C.border}`,
                        textTransform: 'uppercase', letterSpacing: '0.06em',
                      }}
                    >
                      {tip ? (
                        <Tooltip text={tip}>
                          <span>{label}</span>
                          <Info size={9} color={C.dim} style={{ opacity: 0.5 }} />
                        </Tooltip>
                      ) : label}
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {suppliers.map(s => (
                  <SupplierRow
                    key={s.id}
                    supplier={s}
                    onEdit={handleEdit}
                    onDelete={handleDelete}
                    expanded={expandedId === s.id}
                    onToggleExpand={handleToggleExpand}
                  />
                ))}
              </tbody>
            </table>
          </div>
        </div>
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
