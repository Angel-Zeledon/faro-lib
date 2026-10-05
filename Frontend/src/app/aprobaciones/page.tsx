'use client'
/**
 * /aprobaciones — who must approve which purchase orders.
 *
 * Reached from a row in Configuración (admins only). Off until the first rule is
 * added: a tenant that never opens this screen has no approval step anywhere.
 * The screen has two parts and nothing else: the rules ("an order worth X or
 * more needs approval", optionally for one warehouse or supplier) and the
 * people who may approve.
 */
import { useCallback, useEffect, useState } from 'react'
import {
  createPOApprovalRule, deletePOApprovalRule, getPOApprovalSettings, listAdminUsers,
  listSuppliers, listWarehouses, setPOApprover, updatePOApprovalRule,
  type AdminUser,
} from '@/lib/api'
import type { POApprovalRule, POApprovalSettings, Supplier, Warehouse } from '@/lib/types'
import Card from '@/components/ui/Card'
import Input, { Field, Select } from '@/components/ui/Input'
import { LoadingState, useErrorDetail } from '@/components/ui/States'
import { useConfirm } from '@/components/ui/ConfirmDialog'
import { useLanguage } from '@/contexts/LanguageContext'
import { formatMoney } from '@/lib/currency'
import { getUser } from '@/lib/auth'

const C = { border: 'var(--border)', text: 'var(--text)', muted: 'var(--muted)', dim: 'var(--dim)', red: '#C0504D' }

const btn: React.CSSProperties = {
  all: 'unset', cursor: 'pointer', display: 'inline-flex', alignItems: 'center', gap: 4,
  padding: '6px 12px', borderRadius: 8, fontSize: 12, fontWeight: 600,
  border: `1px solid ${C.border}`, color: C.text,
}

export default function ApprovalsSettingsPage() {
  const { t } = useLanguage()
  const errorDetail = useErrorDetail()
  const confirm = useConfirm()
  const isAdmin = getUser()?.role === 'admin'
  const [settings, setSettings] = useState<POApprovalSettings | null>(null)
  const [people, setPeople] = useState<AdminUser[]>([])
  const [warehouses, setWarehouses] = useState<Warehouse[]>([])
  const [suppliers, setSuppliers] = useState<Supplier[]>([])
  const [error, setError] = useState<string | null>(null)
  const [form, setForm] = useState({ threshold: '', self: '', warehouse: '', supplier: '' })
  const [busy, setBusy] = useState(false)

  const load = useCallback(() => {
    getPOApprovalSettings({ silent: true }).then(setSettings).catch(e => setError(errorDetail(e)))
  }, [errorDetail])
  useEffect(() => {
    load()
    if (!isAdmin) return
    listAdminUsers({ limit: 200 }).then(r => setPeople(
      (r.items ?? []).filter((u: AdminUser) => u.status === 'active' && u.role !== 'viewer'))).catch(() => {})
    listWarehouses().then(setWarehouses).catch(() => {})
    listSuppliers({ silent: true }).then(setSuppliers).catch(() => {})
  }, [load, isAdmin])

  async function run(fn: () => Promise<unknown>) {
    setBusy(true); setError(null)
    try { await fn(); load() } catch (e: unknown) { setError(errorDetail(e)) } finally { setBusy(false) }
  }

  if (!isAdmin) {
    return <p style={{ fontSize: 13, color: C.muted }}>{t('po_approval.admin_only')}</p>
  }
  if (!settings) {
    return error ? <p role="alert" style={{ color: C.red, fontSize: 13 }}>{error}</p> : <LoadingState label={t('common.loading')} />
  }

  const approverIds = new Set(settings.approvers.map(a => a.id))
  const thresholdNum = Number(form.threshold)
  const selfNum = form.self.trim() === '' ? null : Number(form.self)
  const formValid = thresholdNum > 0 && (selfNum === null || selfNum > thresholdNum)

  function scopeText(r: POApprovalRule) {
    const parts = [r.warehouse ? t('po_approval.scope_warehouse', { name: r.warehouse }) : null,
                   r.supplier_name ? t('po_approval.scope_supplier', { name: r.supplier_name }) : null]
      .filter(Boolean)
    return parts.length ? parts.join(' · ') : t('po_approval.scope_any')
  }

  return (
    <div style={{ width: '100%', maxWidth: 720, margin: '0 auto', display: 'flex', flexDirection: 'column', gap: 20 }}>
      <p style={{ margin: 0, fontSize: 13, color: C.muted, lineHeight: 1.5 }}>{t('po_approval.settings_intro')}</p>
      {error && <p role="alert" style={{ margin: 0, fontSize: 12.5, color: C.red }}>{error}</p>}

      <Card padding={16}>
        <h2 style={{ margin: '0 0 4px', fontSize: 14, fontWeight: 700 }}>{t('po_approval.approvers_title')}</h2>
        <p style={{ margin: '0 0 12px', fontSize: 12.5, color: C.muted }}>{t('po_approval.approvers_hint')}</p>
        <ul style={{ listStyle: 'none', margin: 0, padding: 0, display: 'flex', flexDirection: 'column', gap: 8 }}>
          {people.map(u => (
            <li key={u.id}>
              <label style={{ display: 'flex', alignItems: 'center', gap: 10, fontSize: 13, cursor: 'pointer' }}>
                <input type="checkbox" checked={approverIds.has(u.id)} disabled={busy}
                       onChange={e => run(() => setPOApprover(u.id, e.target.checked))} />
                <span style={{ overflowWrap: 'anywhere' }}>{u.full_name || u.email}</span>
                <span style={{ color: C.dim, fontSize: 12 }}>{u.email}</span>
              </label>
            </li>
          ))}
        </ul>
      </Card>

      <Card padding={16}>
        <h2 style={{ margin: '0 0 4px', fontSize: 14, fontWeight: 700 }}>{t('po_approval.rules_title')}</h2>
        <p style={{ margin: '0 0 12px', fontSize: 12.5, color: C.muted }}>
          {settings.enabled ? t('po_approval.rules_on') : t('po_approval.rules_off')}
        </p>
        {settings.rules.length > 0 && (
          <ul style={{ listStyle: 'none', margin: '0 0 16px', padding: 0, display: 'flex', flexDirection: 'column', gap: 8 }}>
            {settings.rules.map(r => (
              <li key={r.id} style={{
                border: `1px solid ${C.border}`, borderRadius: 10, padding: 12, background: 'var(--surface-2)',
                opacity: r.active ? 1 : 0.6,
              }}>
                <div style={{ fontSize: 13, fontWeight: 600 }}>
                  {t('po_approval.rule_line', { amount: formatMoney(r.threshold) })}
                </div>
                <div style={{ fontSize: 12, color: C.muted, marginTop: 2 }}>
                  {scopeText(r)}
                  {r.self_approve_below != null && ` · ${t('po_approval.rule_self', { amount: formatMoney(r.self_approve_below) })}`}
                </div>
                <div style={{ display: 'flex', gap: 8, marginTop: 10 }}>
                  <button type="button" style={btn} disabled={busy}
                          onClick={() => run(() => updatePOApprovalRule(r.id, { active: !r.active }))}>
                    {r.active ? t('po_approval.rule_pause') : t('po_approval.rule_resume')}
                  </button>
                  <button type="button" style={btn} disabled={busy} onClick={async () => {
                    if (await confirm({ title: t('po_approval.rule_delete_title'), message: t('po_approval.rule_delete_body'),
                                        confirmLabel: t('po_approval.rule_delete') })) {
                      void run(() => deletePOApprovalRule(r.id))
                    }
                  }}>
                    {t('po_approval.rule_delete')}
                  </button>
                </div>
              </li>
            ))}
          </ul>
        )}

        <h3 style={{ margin: '0 0 8px', fontSize: 12.5, fontWeight: 700, color: C.muted }}>{t('po_approval.rule_add')}</h3>
        <div style={{ display: 'grid', gap: 10, gridTemplateColumns: 'repeat(auto-fit, minmax(200px, 1fr))' }}>
          <Field label={t('po_approval.field_threshold')}>
            <Input type="number" min={0} inputMode="decimal" value={form.threshold}
                   onChange={e => setForm(f => ({ ...f, threshold: e.target.value }))} />
          </Field>
          <Field label={t('po_approval.field_self')} hint={t('po_approval.field_self_hint')}>
            <Input type="number" min={0} inputMode="decimal" value={form.self}
                   onChange={e => setForm(f => ({ ...f, self: e.target.value }))} />
          </Field>
          {warehouses.length > 1 && (
            <Field label={t('po_approval.field_warehouse')}>
              <Select value={form.warehouse} onChange={e => setForm(f => ({ ...f, warehouse: e.target.value }))}>
                <option value="">{t('po_approval.scope_any')}</option>
                {warehouses.map(w => <option key={w.name} value={w.name}>{w.name}</option>)}
              </Select>
            </Field>
          )}
          {suppliers.length > 0 && (
            <Field label={t('po_approval.field_supplier')}>
              <Select value={form.supplier} onChange={e => setForm(f => ({ ...f, supplier: e.target.value }))}>
                <option value="">{t('po_approval.scope_any')}</option>
                {suppliers.map(s => <option key={s.id} value={s.id}>{s.name}</option>)}
              </Select>
            </Field>
          )}
        </div>
        {selfNum !== null && selfNum <= thresholdNum && thresholdNum > 0 && (
          <p style={{ margin: '8px 0 0', fontSize: 12, color: C.red }}>{t('po_approval.field_self_invalid')}</p>
        )}
        <div style={{ marginTop: 12 }}>
          <button type="button" style={{ ...btn, opacity: formValid && !busy ? 1 : 0.5 }}
                  disabled={!formValid || busy}
                  onClick={() => run(async () => {
                    await createPOApprovalRule({
                      threshold: thresholdNum, self_approve_below: selfNum,
                      warehouse: form.warehouse || null, supplier_id: form.supplier || null,
                    })
                    setForm({ threshold: '', self: '', warehouse: '', supplier: '' })
                  })}>
            {t('po_approval.rule_add_btn')}
          </button>
        </div>
      </Card>
    </div>
  )
}
