'use client'
/**
 * The semáforo's rules, in the buyer's words — and editable.
 *
 * Two of the signal's boundaries are multiples of the supplier's lead time
 * (PEDIR YA below 0.5x, SOBRESTOCK from 3x by default); the third, PEDIR
 * PRONTO, is the reorder point and is stated here but deliberately NOT a
 * knob — see backend/inventory/signal_thresholds.py for why.
 *
 * Three things the panel owes the user before they save:
 *  - the rule as a sentence with numbers in it, for a supplier they can picture
 *    ("con un proveedor que tarda 10 días: PEDIR YA si te quedan menos de 5");
 *  - how many of THEIR products would change colour, computed by the server
 *    running the real semáforo with the candidate values (nothing saved);
 *  - which values are the factory ones and which somebody chose — a default
 *    must never look like a decision.
 */
import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { AlertTriangle, Info, RotateCcw, Save, Trash2 } from 'lucide-react'

import Button from '@/components/ui/Button'
import Spinner from '@/components/ui/Spinner'
import { signalColor } from '@/components/ui/SignalBadge'
import { useErrorDetail } from '@/components/ui/States'
import { useConfirm } from '@/components/ui/ConfirmDialog'
import { useToast } from '@/contexts/ToastContext'
import { useLanguage } from '@/contexts/LanguageContext'
import {
  getSignalThresholds, listSuppliers, previewSignalThresholds,
  resetSignalThresholds, saveSignalThresholds,
} from '@/lib/api'
import { getUser } from '@/lib/auth'
import { localeFor } from '@/lib/numberLocale'
import type {
  InventorySignal, SignalThresholdFactors, SignalThresholdScope,
  SignalThresholdsPreview, SignalThresholdsState, Supplier,
} from '@/lib/types'

const SIGNAL_KEY: Record<InventorySignal, string> = {
  PEDIR_YA:     'inventory.signal_order_now',
  PEDIR_PRONTO: 'inventory.signal_order_soon',
  OK:           'inventory.signal_ok',
  SOBRESTOCK:   'inventory.signal_overstock',
  SIN_DATOS:    'inventory.signal_sin_datos',
}
const SIGNAL_COLOR: Record<string, string> = {
  PEDIR_YA: signalColor('PEDIR_YA'),
  PEDIR_PRONTO: signalColor('PEDIR_PRONTO'),
  OK: signalColor('OK'),
  SOBRESTOCK: signalColor('SOBRESTOCK'),
}

type Target = { scope: SignalThresholdScope; name: string | null }
type Draft = { order_now: string; overstock: string }

// Shown with the reader's decimal mark ("0,5" in Spanish); `parse` accepts both.
const toDraft = (f: SignalThresholdFactors, lang: string): Draft => {
  const show = (n: number) => n.toLocaleString(localeFor(lang), { maximumFractionDigits: 2, useGrouping: false })
  return { order_now: show(f.order_now_factor), overstock: show(f.overstock_factor) }
}

const parse = (s: string): number => Number(s.trim().replace(',', '.'))

export default function SignalThresholdsPanel() {
  const { t, lang } = useLanguage()
  const errorDetail = useErrorDetail()
  const confirm = useConfirm()
  const { addToast } = useToast()
  const canEdit = (() => {
    const role = getUser()?.role
    return role === 'admin' || role === 'analyst'
  })()

  const [state, setState]         = useState<SignalThresholdsState | null>(null)
  const [loadFailed, setLoadFailed] = useState(false)
  const [suppliers, setSuppliers] = useState<Supplier[]>([])
  const [target, setTarget]       = useState<Target>({ scope: 'global', name: null })
  const [draft, setDraft]         = useState<Draft>({ order_now: '', overstock: '' })
  const [exampleLead, setExampleLead] = useState('10')
  const [preview, setPreview]     = useState<SignalThresholdsPreview | null>(null)
  const [previewing, setPreviewing] = useState(false)
  const [previewError, setPreviewError] = useState<string | null>(null)
  const [saving, setSaving]       = useState(false)
  const [saveError, setSaveError] = useState<string | null>(null)

  const num = useCallback((n: number, digits = 2) =>
    n.toLocaleString(localeFor(lang), { maximumFractionDigits: digits }), [lang])

  const load = useCallback(async () => {
    try {
      const s = await getSignalThresholds({ silent: true })
      setState(s)
      setLoadFailed(false)
      return s
    } catch {
      setLoadFailed(true)
      return null
    }
  }, [])

  useEffect(() => {
    void load()
    listSuppliers({ silent: true }).then(setSuppliers).catch(() => setSuppliers([]))
  }, [load])

  // What the selected scope says TODAY: its own rule, or what it inherits.
  const stored = useMemo<SignalThresholdFactors | null>(() => {
    if (!state) return null
    if (target.scope === 'global') return state.effective
    const own = state.overrides.find(o =>
      o.scope_type === target.scope && o.scope_value === (target.name ?? '').trim().toLowerCase())
    return own ?? state.effective
  }, [state, target])

  const ownOverride = useMemo(() => state?.overrides.find(o =>
    o.scope_type === target.scope && o.scope_value === (target.name ?? '').trim().toLowerCase()),
  [state, target])

  // Re-seed the inputs when the user switches what they are editing.
  useEffect(() => { if (stored) setDraft(toDraft(stored, lang)) }, [stored, lang])

  const values: SignalThresholdFactors = {
    order_now_factor: parse(draft.order_now),
    overstock_factor: parse(draft.overstock),
  }
  const bounds = state?.bounds
  const localProblem = useMemo(() => {
    if (!bounds) return null
    for (const [key, field] of [['order_now_factor', 'order_now'], ['overstock_factor', 'overstock']] as const) {
      const v = parse(draft[field])
      const b = bounds[key]
      if (!Number.isFinite(v) || v < b.min || v > b.max) {
        return t('inventory.thresholds_out_of_range', {
          label: t(key === 'order_now_factor' ? 'inventory.signal_order_now' : 'inventory.signal_overstock'),
          min: num(b.min), max: num(b.max),
        })
      }
    }
    return null
  }, [bounds, draft, t, num])

  const dirty = !!stored && (
    values.order_now_factor !== stored.order_now_factor
    || values.overstock_factor !== stored.overstock_factor
    || (target.scope !== 'global' && !ownOverride)
  )
  const needsName = target.scope !== 'global' && !(target.name ?? '').trim()

  // Preview: debounced, only for a valid, changed draft. The answer comes from
  // the server running the real semáforo — never a client-side guess.
  const seq = useRef(0)
  useEffect(() => {
    setPreview(null)
    setPreviewError(null)
    if (!dirty || localProblem || needsName) return
    const mine = ++seq.current
    setPreviewing(true)
    const h = setTimeout(() => {
      previewSignalThresholds({
        ...values, scope_type: target.scope, scope_value: target.name,
      }, { silent: true })
        .then(p => { if (mine === seq.current) setPreview(p) })
        .catch(e => { if (mine === seq.current) setPreviewError(errorDetail(e) || t('inventory.thresholds_preview_failed')) })
        .finally(() => { if (mine === seq.current) setPreviewing(false) })
    }, 450)
    return () => clearTimeout(h)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [draft.order_now, draft.overstock, target.scope, target.name, dirty, localProblem, needsName])

  async function save() {
    if (!canEdit || localProblem || needsName) return
    setSaving(true)
    setSaveError(null)
    try {
      const s = await saveSignalThresholds({
        ...values, scope_type: target.scope, scope_value: target.name,
      })
      setState(s)
      setPreview(null)
      addToast(
        target.scope === 'global'
          ? t('inventory.thresholds_saved')
          : t('inventory.thresholds_override_saved', { name: target.name ?? '' }),
        t('inventory.thresholds_saved_body'), 'success')
    } catch (e) {
      setSaveError(errorDetail(e))
    } finally {
      setSaving(false)
    }
  }

  async function reset(scope: SignalThresholdScope, name: string | null) {
    if (!canEdit || !state) return
    // Say what the reset will do to THEIR products before doing it.
    let p: SignalThresholdsPreview | null = null
    try {
      p = await previewSignalThresholds({ scope_type: scope, scope_value: name, reset: true }, { silent: true })
    } catch { p = null }
    const fallback = scope === 'global' ? state.defaults : state.effective
    const params = {
      order_now: num(fallback.order_now_factor), overstock: num(fallback.overstock_factor),
      changed: p?.changed ?? 0, total: p?.total ?? 0, name: name ?? '',
    }
    const ok = await confirm({
      title: scope === 'global'
        ? t('inventory.thresholds_reset_confirm_title')
        : t('inventory.thresholds_override_remove_confirm_title', params),
      message: p?.available
        ? t(scope === 'global' ? 'inventory.thresholds_reset_confirm_body'
                               : 'inventory.thresholds_override_remove_confirm_body', params)
        : t(scope === 'global' ? 'inventory.thresholds_reset_confirm_body_unknown'
                               : 'inventory.thresholds_override_remove_confirm_body_unknown', params),
      confirmLabel: scope === 'global' ? t('inventory.thresholds_reset') : t('inventory.thresholds_override_remove'),
    })
    if (!ok) return
    try {
      const s = await resetSignalThresholds(scope, name)
      setState(s)
      if (scope !== 'global' && target.scope === scope && target.name === name) {
        setTarget({ scope: 'global', name: null })
      }
      addToast(
        scope === 'global' ? t('inventory.thresholds_reset_done')
                           : t('inventory.thresholds_override_removed', { name: name ?? '' }),
        '', 'success')
    } catch (e) {
      setSaveError(errorDetail(e))
    }
  }

  if (!state) {
    return (
      <section style={sectionStyle}>
        {loadFailed
          ? <div style={{ color: 'var(--dim)', fontSize: 13 }}>{t('inventory.thresholds_load_failed')}</div>
          : <div style={{ textAlign: 'center' }}><Spinner size={18} /></div>}
      </section>
    )
  }

  const lead = Math.max(1, Math.min(365, parse(exampleLead) || 10))
  const ex = {
    orderNow: num(lead * (Number.isFinite(values.order_now_factor) ? values.order_now_factor : 0), 1),
    overstock: num(lead * (Number.isFinite(values.overstock_factor) ? values.overstock_factor : 0), 1),
    lead: num(lead, 0),
  }
  const isDefault = state.source === 'default'

  return (
    <section style={sectionStyle} id="reglas-semaforo">
      <h2 style={{ fontSize: 15, fontWeight: 700, color: 'var(--text)', margin: 0 }}>
        {t('inventory.thresholds_title')}
      </h2>
      <p style={{ fontSize: 13, color: 'var(--muted)', margin: '6px 0 4px', lineHeight: 1.6, maxWidth: 760 }}>
        {t('inventory.thresholds_intro')}
      </p>
      <p style={{ fontSize: 12, color: 'var(--dim)', margin: '0 0 14px' }}>
        {isDefault
          ? t('inventory.thresholds_source_default')
          : t('inventory.thresholds_source_tenant', {
              date: state.tenant?.updated_at
                ? new Date(state.tenant.updated_at).toLocaleDateString(localeFor(lang)) : '—',
            })}
      </p>

      {/* What is being edited: the company rule, or one supplier's exception. */}
      <div style={{ display: 'flex', gap: 8, alignItems: 'center', flexWrap: 'wrap', marginBottom: 12 }}>
        <label style={{ fontSize: 12, color: 'var(--muted)' }}>{t('inventory.thresholds_apply_to')}</label>
        <select
          aria-label={t('inventory.thresholds_apply_to')}
          value={target.scope === 'global' ? '__global__' : `${target.scope}:${target.name ?? ''}`}
          onChange={e => {
            const v = e.target.value
            if (v === '__global__') setTarget({ scope: 'global', name: null })
            else {
              const [scope, ...rest] = v.split(':')
              setTarget({ scope: scope as SignalThresholdScope, name: rest.join(':') || null })
            }
          }}
          style={selectStyle}
        >
          <option value="__global__">{t('inventory.thresholds_scope_company')}</option>
          {state.overrides.filter(o => o.scope_type === 'category').map(o => (
            <option key={`c-${o.scope_value}`} value={`category:${o.scope_value}`}>
              {t('inventory.thresholds_override_scope_category')}: {o.scope_value}
            </option>
          ))}
          {suppliers.map(s => (
            <option key={s.id} value={`supplier:${s.name}`}>
              {t('inventory.thresholds_override_scope_supplier')}: {s.name}
              {state.overrides.some(o => o.scope_type === 'supplier' && o.scope_value === s.name.trim().toLowerCase())
                ? ` · ${t('inventory.thresholds_has_exception')}` : ''}
            </option>
          ))}
        </select>
      </div>

      {target.scope !== 'global' && !ownOverride && (
        <p style={{ fontSize: 12, color: 'var(--dim)', margin: '0 0 10px' }}>
          {t('inventory.thresholds_inherits', { name: target.name ?? '' })}
        </p>
      )}

      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(260px, 1fr))', gap: 12 }}>
        <FactorField
          color={SIGNAL_COLOR.PEDIR_YA}
          label={t('inventory.thresholds_order_now_label')}
          suffix={t('inventory.thresholds_times_lead')}
          value={draft.order_now}
          onChange={v => setDraft(d => ({ ...d, order_now: v }))}
          disabled={!canEdit}
          hint={t('inventory.thresholds_field_hint', {
            def: num(state.defaults.order_now_factor),
            min: num(state.bounds.order_now_factor.min), max: num(state.bounds.order_now_factor.max),
          })}
          testId="threshold-order-now"
        />
        <FactorField
          color={SIGNAL_COLOR.SOBRESTOCK}
          label={t('inventory.thresholds_overstock_label')}
          suffix={t('inventory.thresholds_times_lead_or_more')}
          value={draft.overstock}
          onChange={v => setDraft(d => ({ ...d, overstock: v }))}
          disabled={!canEdit}
          hint={t('inventory.thresholds_field_hint', {
            def: num(state.defaults.overstock_factor),
            min: num(state.bounds.overstock_factor.min), max: num(state.bounds.overstock_factor.max),
          })}
          testId="threshold-overstock"
        />
      </div>

      <div style={{
        display: 'flex', gap: 8, alignItems: 'flex-start', marginTop: 10,
        fontSize: 12, color: 'var(--muted)', lineHeight: 1.55, maxWidth: 820,
      }}>
        <Info size={13} style={{ flexShrink: 0, marginTop: 2 }} />
        <span>{t('inventory.thresholds_order_soon_fixed')}</span>
      </div>

      {/* The rule, worked through for a supplier the user can picture. */}
      <div style={{
        marginTop: 14, padding: '12px 14px', borderRadius: 9,
        background: 'var(--surface-2)', border: '1px solid var(--border)',
      }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 6, flexWrap: 'wrap', fontSize: 13, color: 'var(--text)', fontWeight: 600 }}>
          <span>{t('inventory.thresholds_example_title')}</span>
          <input
            aria-label={t('inventory.thresholds_example_title')}
            value={exampleLead}
            onChange={e => setExampleLead(e.target.value)}
            inputMode="numeric"
            style={{ ...inputStyle, width: 52 }}
          />
          <span>{t('inventory.thresholds_example_days_unit')}:</span>
        </div>
        {localProblem ? (
          <div style={{ marginTop: 8, fontSize: 12.5, color: '#f59e0b' }}>{localProblem}</div>
        ) : (
          <ul style={{ margin: '8px 0 0', paddingLeft: 18, fontSize: 12.5, color: 'var(--muted)', lineHeight: 1.75 }}>
            <li><strong style={{ color: SIGNAL_COLOR.PEDIR_YA }}>{t('inventory.signal_order_now')}</strong>{' '}
              {t('inventory.thresholds_example_order_now', { days: ex.orderNow })}</li>
            <li><strong style={{ color: SIGNAL_COLOR.PEDIR_PRONTO }}>{t('inventory.signal_order_soon')}</strong>{' '}
              {t('inventory.thresholds_example_order_soon', { lead: ex.lead })}</li>
            <li><strong style={{ color: SIGNAL_COLOR.OK }}>{t('inventory.signal_ok')}</strong>{' '}
              {t('inventory.thresholds_example_ok', { days: ex.overstock })}</li>
            <li><strong style={{ color: SIGNAL_COLOR.SOBRESTOCK }}>{t('inventory.signal_overstock')}</strong>{' '}
              {t('inventory.thresholds_example_overstock', { days: ex.overstock })}</li>
          </ul>
        )}
      </div>

      {/* Before saving: what happens to THEIR products. */}
      <div style={{ marginTop: 12, minHeight: 22 }} data-testid="threshold-preview">
        {!dirty ? (
          <span style={{ fontSize: 12, color: 'var(--dim)' }}>{t('inventory.thresholds_preview_hint')}</span>
        ) : needsName || localProblem ? null : previewing && !preview ? (
          <span style={{ fontSize: 12, color: 'var(--dim)', display: 'inline-flex', gap: 6, alignItems: 'center' }}>
            <Spinner size={11} /> {t('inventory.thresholds_preview_loading')}
          </span>
        ) : previewError ? (
          <span style={{ fontSize: 12, color: '#f59e0b' }}>{previewError}</span>
        ) : preview && !preview.available ? (
          <span style={{ fontSize: 12, color: 'var(--dim)' }}>{t('inventory.thresholds_preview_no_session')}</span>
        ) : preview ? (
          <PreviewSummary preview={preview} />
        ) : null}
      </div>

      {saveError && (
        <div role="alert" style={{ marginTop: 10, display: 'flex', gap: 6, fontSize: 12.5, color: '#ef4444' }}>
          <AlertTriangle size={13} style={{ marginTop: 2 }} /> {saveError}
        </div>
      )}

      {canEdit ? (
        <div style={{ display: 'flex', gap: 8, marginTop: 14, flexWrap: 'wrap' }}>
          <Button variant="primary" size="sm" icon={<Save size={12} />} loading={saving}
                  disabled={!dirty || !!localProblem || needsName || saving}
                  onClick={() => void save()} data-testid="threshold-save">
            {target.scope === 'global' ? t('inventory.thresholds_save') : t('inventory.thresholds_save_override')}
          </Button>
          {target.scope === 'global' && !isDefault && (
            <Button variant="ghost" size="sm" icon={<RotateCcw size={12} />}
                    onClick={() => void reset('global', null)} data-testid="threshold-reset">
              {t('inventory.thresholds_reset')}
            </Button>
          )}
        </div>
      ) : (
        <p style={{ fontSize: 12, color: 'var(--dim)', marginTop: 12 }}>{t('inventory.thresholds_viewer_note')}</p>
      )}

      {/* Exceptions that exist today, so none of them is a surprise. */}
      <div style={{ marginTop: 18, borderTop: '1px solid var(--border)', paddingTop: 12 }}>
        <div style={{ fontSize: 13, fontWeight: 600, color: 'var(--text)' }}>{t('inventory.thresholds_overrides_title')}</div>
        <p style={{ fontSize: 12, color: 'var(--dim)', margin: '4px 0 8px', lineHeight: 1.55, maxWidth: 760 }}>
          {t('inventory.thresholds_overrides_intro')}
        </p>
        {state.overrides.length === 0 ? (
          <div style={{ fontSize: 12, color: 'var(--dim)' }}>{t('inventory.thresholds_overrides_empty')}</div>
        ) : (
          <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
            {state.overrides.map(o => (
              <div key={`${o.scope_type}:${o.scope_value}`} style={{
                display: 'flex', alignItems: 'center', gap: 10, flexWrap: 'wrap',
                padding: '7px 10px', borderRadius: 8, background: 'var(--surface-2)', border: '1px solid var(--border)',
              }}>
                <span style={{ fontSize: 12.5, fontWeight: 600, color: 'var(--text)' }}>
                  {t(o.scope_type === 'supplier' ? 'inventory.thresholds_override_scope_supplier'
                                                  : 'inventory.thresholds_override_scope_category')}: {displayName(o.scope_value, suppliers)}
                </span>
                <span style={{ fontSize: 12, color: 'var(--muted)', flex: 1 }}>
                  {t('inventory.thresholds_override_row', {
                    order_now: num(o.order_now_factor), overstock: num(o.overstock_factor),
                  })}
                </span>
                {canEdit && (
                  <Button variant="danger" size="sm" icon={<Trash2 size={12} />}
                          onClick={() => void reset(o.scope_type, displayName(o.scope_value, suppliers))}>
                    {t('inventory.thresholds_override_remove')}
                  </Button>
                )}
              </div>
            ))}
          </div>
        )}
      </div>
    </section>
  )

  function PreviewSummary({ preview }: { preview: SignalThresholdsPreview }) {
    if (!preview.changed) {
      return <span style={{ fontSize: 12.5, color: 'var(--muted)' }}>
        {t('inventory.thresholds_preview_none', { total: preview.total ?? 0 })}
      </span>
    }
    const moves = Object.entries(preview.transitions ?? {}).sort((a, b) => b[1] - a[1])
    return (
      <div style={{ fontSize: 12.5, color: 'var(--text)' }}>
        <div style={{ fontWeight: 600 }}>
          {t(preview.changed === 1 ? 'inventory.thresholds_preview_changed_one'
                                   : 'inventory.thresholds_preview_changed',
             { changed: preview.changed, total: preview.total ?? 0 })}
        </div>
        <ul style={{ margin: '4px 0 0', paddingLeft: 18, color: 'var(--muted)', lineHeight: 1.7 }}>
          {moves.map(([k, count]) => {
            const [from, to] = k.split('>') as [InventorySignal, InventorySignal]
            return (
              <li key={k}>
                {t(count === 1 ? 'inventory.thresholds_preview_move_one' : 'inventory.thresholds_preview_move', {
                  count, from: t(SIGNAL_KEY[from] ?? from), to: t(SIGNAL_KEY[to] ?? to),
                })}
              </li>
            )
          })}
        </ul>
        {!!preview.sample?.length && (
          <div style={{ marginTop: 4, fontSize: 11.5, color: 'var(--dim)' }}>
            {t('inventory.thresholds_preview_examples')}{' '}
            {preview.sample.map(s => s.name || s.sku).join(', ')}
          </div>
        )}
      </div>
    )
  }
}

/** Overrides are stored lower-cased; show the supplier's real spelling when
 *  we know it. */
function displayName(stored: string | null, suppliers: Supplier[]): string {
  const hit = suppliers.find(s => s.name.trim().toLowerCase() === (stored ?? ''))
  return hit?.name ?? stored ?? ''
}

function FactorField({ color, label, suffix, value, onChange, disabled, hint, testId }: {
  color: string; label: string; suffix: string; value: string
  onChange: (v: string) => void; disabled: boolean; hint: string; testId: string
}) {
  return (
    <div style={{ padding: '10px 12px', borderRadius: 9, border: '1px solid var(--border)', borderLeft: `3px solid ${color}` }}>
      <div style={{ fontSize: 12.5, color: 'var(--text)', fontWeight: 600, marginBottom: 6 }}>{label}</div>
      <div style={{ display: 'flex', alignItems: 'center', gap: 8, flexWrap: 'wrap' }}>
        <input
          aria-label={label}
          data-testid={testId}
          value={value}
          onChange={e => onChange(e.target.value)}
          disabled={disabled}
          inputMode="decimal"
          style={{ ...inputStyle, width: 64 }}
        />
        <span style={{ fontSize: 12.5, color: 'var(--muted)' }}>{suffix}</span>
      </div>
      <div style={{ fontSize: 11, color: 'var(--dim)', marginTop: 6 }}>{hint}</div>
    </div>
  )
}

const sectionStyle: React.CSSProperties = {
  border: '1px solid var(--border)', borderRadius: 12,
  background: 'var(--surface)', padding: '18px 20px',
}
const inputStyle: React.CSSProperties = {
  padding: '5px 8px', borderRadius: 6, border: '1px solid var(--border)',
  background: 'var(--surface)', color: 'var(--text)', fontSize: 13,
}
const selectStyle: React.CSSProperties = { ...inputStyle, minWidth: 220 }
