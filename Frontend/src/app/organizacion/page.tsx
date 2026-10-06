'use client'
/**
 * /organizacion — a holding and its subsidiaries.
 *
 * Three jobs on one screen, each shown only to who can use it:
 *   · the consolidated READ-ONLY views (commitments, stock by signal, purchase
 *     orders, budget vs spend) for a person who was given access to at least one
 *     subsidiary;
 *   · linking subsidiaries and giving people access (administrators of the
 *     holding): create a one-time code, hand it to the subsidiary;
 *   · joining an organization (administrators of the subsidiary): paste the
 *     code, or end an existing link.
 *
 * Nothing here writes into another company. Which companies a view covers is
 * decided by the server from the person's grants; this screen never sends a
 * company id to read. Rules the screen keeps visible instead of hiding: money
 * is never added across currencies, a company whose figures are stale is listed
 * as not counted, and a company that could not be read is named.
 */
import { useCallback, useEffect, useMemo, useState } from 'react'

import {
  acceptOrgLink, createOrgLink, getOrgBudgets, getOrgCommittedDemand, getOrgOverview, getOrgPurchaseOrders,
  getOrgStockSignals, grantOrgLinkMember, listAdminUsers, listOrgLinkMembers, listOrgLinks, removeOrgLinkMember,
  revokeOrgLink, type AdminUser,
} from '@/lib/api'
import type {
  OrgBudgets, OrgCommittedDemand, OrgCreatedLink, OrgLinkAsParent, OrgLinks, OrgMember, OrgMoney,
  OrgOverview, OrgPurchaseOrders, OrgStockSignals, OrgUnavailable,
} from '@/lib/orgTypes'
import { SIGNAL_ORDER, SIGNAL_STYLES } from '@/components/ui/SignalBadge'
import Card from '@/components/ui/Card'
import Input, { Field, Select } from '@/components/ui/Input'
import Table, { Td, Th, Tr } from '@/components/ui/Table'
import { EmptyState, LoadingState, useErrorDetail } from '@/components/ui/States'
import { useConfirm } from '@/components/ui/ConfirmDialog'
import { useLanguage } from '@/contexts/LanguageContext'

const C = { border: 'var(--border)', text: 'var(--text)', muted: 'var(--muted)', dim: 'var(--dim)', red: '#C0504D' }

const btn: React.CSSProperties = {
  all: 'unset', cursor: 'pointer', display: 'inline-flex', alignItems: 'center', gap: 4,
  padding: '6px 12px', borderRadius: 8, fontSize: 12, fontWeight: 600,
  border: `1px solid ${C.border}`, color: C.text,
}

type ViewKey = 'committed' | 'stock' | 'orders' | 'budgets'
const TAB_LABEL: Record<ViewKey, string> = {
  committed: 'org.tab_committed', stock: 'org.tab_stock', orders: 'org.tab_orders', budgets: 'org.tab_budgets',
}
const ZERO_DECIMAL = new Set(['CRC', 'COP', 'CLP', 'ARS'])

function useFormatters() {
  const { lang } = useLanguage()
  const num = (n: number) => n.toLocaleString(lang, { maximumFractionDigits: 2 })
  const money = (list: OrgMoney[]) => {
    const parts = list.map(m => {
      try {
        return new Intl.NumberFormat(lang, {
          style: 'currency', currency: m.currency,
          maximumFractionDigits: ZERO_DECIMAL.has(m.currency) ? 0 : 2,
        }).format(m.amount)
      } catch { return `${m.currency} ${num(m.amount)}` }
    })
    return parts.length ? parts.join(' · ') : '—'
  }
  const one = (amount: number, currency: string) => money([{ currency, amount }])
  const date = (iso: string | null) => (iso ? new Date(iso).toLocaleDateString(lang) : '—')
  return { num, money, one, date }
}

const Note = ({ children, tone }: { children: React.ReactNode; tone?: 'warn' }) => (
  <p style={{ margin: '8px 0 0', fontSize: 12, color: tone === 'warn' ? C.red : C.muted, lineHeight: 1.5 }}>{children}</p>
)

function UnavailableNote({ items }: { items: OrgUnavailable[] }) {
  const { t } = useLanguage()
  if (!items.length) return null
  return <Note tone="warn">{t('org.unavailable_note', { names: items.map(i => i.label).join(', ') })}</Note>
}

// ── Consolidated views ───────────────────────────────────────────────────────

function Consolidated({ overview }: { overview: OrgOverview }) {
  const { t } = useLanguage()
  const errorDetail = useErrorDetail()
  const f = useFormatters()
  const [view, setView] = useState<ViewKey>('committed')
  const [days, setDays] = useState(90)
  const [data, setData] = useState<unknown>(null)
  const [error, setError] = useState<string | null>(null)
  const [loading, setLoading] = useState(true)
  const [tick, setTick] = useState(0)

  useEffect(() => {
    let live = true
    setLoading(true); setError(null); setData(null)
    const call = {
      committed: () => getOrgCommittedDemand(),
      stock: () => getOrgStockSignals(),
      orders: () => getOrgPurchaseOrders(days),
      budgets: () => getOrgBudgets(),
    }[view]
    call().then(d => { if (live) setData(d) })
      .catch(e => { if (live) setError(errorDetail(e) || t('org.load_error')) })
      .finally(() => { if (live) setLoading(false) })
    return () => { live = false }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [view, days, tick])

  const tabs: ViewKey[] = ['committed', 'stock', 'orders', 'budgets']
  return (
    <Card padding={16}>
      <div style={{ display: 'flex', flexWrap: 'wrap', alignItems: 'center', gap: 8, justifyContent: 'space-between' }}>
        <h2 style={{ margin: 0, fontSize: 14, fontWeight: 700 }}>{t('org.consolidated_title')}</h2>
        <button type="button" style={btn} onClick={() => setTick(n => n + 1)}>{t('org.refresh')}</button>
      </div>
      <p style={{ margin: '6px 0 12px', fontSize: 12, color: C.muted }}>
        {t('org.covers')}: {overview.tenants.map(x => x.is_own ? `${x.name} (${t('org.own_suffix')})` : (x.label || x.name)).join(', ')}
      </p>
      <div role="tablist" style={{ display: 'flex', flexWrap: 'wrap', gap: 6, marginBottom: 12 }}>
        {tabs.map(k => (
          <button key={k} type="button" role="tab" aria-selected={view === k} onClick={() => setView(k)}
                  style={{ ...btn, background: view === k ? 'var(--surface-2)' : 'transparent',
                           borderColor: view === k ? 'var(--accent)' : C.border }}>
            {t(TAB_LABEL[k])}
          </button>
        ))}
      </div>
      {view === 'orders' && (
        <div style={{ marginBottom: 12, maxWidth: 220 }}>
          <Select value={days} onChange={e => setDays(Number(e.target.value))} aria-label={t('org.days', { days })}>
            {[30, 90, 180, 365].map(d => <option key={d} value={d}>{t('org.days', { days: d })}</option>)}
          </Select>
        </div>
      )}
      {loading && <LoadingState label={t('common.loading')} />}
      {error && <p role="alert" style={{ margin: 0, fontSize: 12.5, color: C.red }}>{error}</p>}
      {!loading && !error && data != null && (
        <>
          {view === 'committed' && <CommittedView d={data as OrgCommittedDemand} f={f} />}
          {view === 'stock' && <StockView d={data as OrgStockSignals} f={f} />}
          {view === 'orders' && <OrdersView d={data as OrgPurchaseOrders} f={f} />}
          {view === 'budgets' && <BudgetsView d={data as OrgBudgets} f={f} />}
        </>
      )}
    </Card>
  )
}

type F = ReturnType<typeof useFormatters>

function CommittedView({ d, f }: { d: OrgCommittedDemand; f: F }) {
  const { t } = useLanguage()
  return (
    <>
      <p style={{ margin: '0 0 8px', fontSize: 12, color: C.muted }}>{t('org.committed_hint')}</p>
      <Table minWidth={640}>
        <thead><tr>
          <Th>{t('org.col_company')}</Th><Th align="right">{t('org.col_commitments')}</Th>
          <Th align="right">{t('org.col_units')}</Th><Th align="right">{t('org.col_weighted')}</Th>
          <Th align="right">{t('org.col_overdue')}</Th><Th>{t('org.col_next')}</Th>
        </tr></thead>
        <tbody>
          {d.tenants.map(r => (
            <Tr key={r.tenant_id}>
              <Td>{r.name}</Td><Td align="right" mono>{f.num(r.commitments)}</Td>
              <Td align="right" mono>{f.num(r.quantity)}</Td><Td align="right" mono>{f.num(r.weighted_quantity)}</Td>
              <Td align="right" mono>{f.num(r.overdue)}</Td><Td>{f.date(r.earliest_delivery)}</Td>
            </Tr>
          ))}
          <Tr>
            <Td><strong>{t('org.col_total')}</strong></Td>
            <Td align="right" mono><strong>{f.num(d.totals.commitments)}</strong></Td>
            <Td align="right" mono><strong>{f.num(d.totals.quantity)}</strong></Td>
            <Td align="right" mono><strong>{f.num(d.totals.weighted_quantity)}</strong></Td>
            <Td align="right" mono><strong>{f.num(d.totals.overdue)}</strong></Td><Td>{' '}</Td>
          </Tr>
        </tbody>
      </Table>
      {d.months.length > 0 && (
        <>
          <h3 style={{ margin: '16px 0 6px', fontSize: 12.5, fontWeight: 700, color: C.muted }}>{t('org.months_title')}</h3>
          <Table minWidth={420}>
            <thead><tr>
              <Th>{t('org.col_period')}</Th><Th align="right">{t('org.col_commitments')}</Th>
              <Th align="right">{t('org.col_units')}</Th><Th align="right">{t('org.col_weighted')}</Th>
            </tr></thead>
            <tbody>
              {d.months.map(m => (
                <Tr key={m.month}>
                  <Td>{m.month}</Td><Td align="right" mono>{f.num(m.commitments)}</Td>
                  <Td align="right" mono>{f.num(m.quantity)}</Td><Td align="right" mono>{f.num(m.weighted_quantity)}</Td>
                </Tr>
              ))}
            </tbody>
          </Table>
        </>
      )}
      <UnavailableNote items={d.unavailable} />
    </>
  )
}

function StockView({ d, f }: { d: OrgStockSignals; f: F }) {
  const { t } = useLanguage()
  const sig = (s: string) => (SIGNAL_STYLES as Record<string, { labelKey: string }>)[s]
  const cols = SIGNAL_ORDER.filter(s => s !== 'SIN_DATOS' || (d.totals.signals[s] ?? 0) > 0)
  return (
    <>
      <p style={{ margin: '0 0 8px', fontSize: 12, color: C.muted }}>{t('org.stock_hint')}</p>
      <Table minWidth={760}>
        <thead><tr>
          <Th>{t('org.col_company')}</Th><Th>{t('org.col_state')}</Th><Th align="right">{t('org.col_skus')}</Th>
          {cols.map(s => <Th key={s} align="right">{sig(s) ? t(sig(s).labelKey) : s}</Th>)}
          <Th align="right">{t('org.col_value')}</Th>
        </tr></thead>
        <tbody>
          {d.tenants.map(r => (
            <Tr key={r.tenant_id} style={{ opacity: r.counted ? 1 : 0.65 }}>
              <Td>{r.name}</Td><Td>{t(`org.state.${r.state}`)}</Td>
              <Td align="right" mono>{r.skus != null ? f.num(r.skus) : '—'}</Td>
              {cols.map(s => <Td key={s} align="right" mono>{r.signals ? f.num(r.signals[s] ?? 0) : '—'}</Td>)}
              <Td align="right" mono>{r.inventory_value ? f.money(r.inventory_value) : '—'}</Td>
            </Tr>
          ))}
          <Tr>
            <Td><strong>{t('org.col_total')}</strong></Td><Td>{' '}</Td>
            <Td align="right" mono><strong>{f.num(d.totals.skus)}</strong></Td>
            {cols.map(s => <Td key={s} align="right" mono><strong>{f.num(d.totals.signals[s] ?? 0)}</strong></Td>)}
            <Td align="right" mono><strong>{f.money(d.totals.inventory_value)}</strong></Td>
          </Tr>
        </tbody>
      </Table>
      {d.totals.skus_without_value > 0 && <Note>{t('org.skus_without_value', { count: d.totals.skus_without_value })}</Note>}
      <Note>{t('org.money_per_currency')}</Note>
      {d.excluded.length > 0 && (
        <div style={{ marginTop: 12 }}>
          <h3 style={{ margin: '0 0 4px', fontSize: 12.5, fontWeight: 700, color: C.red }}>{t('org.excluded_title')}</h3>
          <ul style={{ margin: 0, paddingLeft: 18, fontSize: 12.5 }}>
            {d.excluded.map(e => <li key={e.tenant_id}>{e.name}: {t(`org.state.${e.reason}`)}</li>)}
          </ul>
          <Note>{t('org.excluded_hint')}</Note>
        </div>
      )}
      <UnavailableNote items={d.unavailable} />
    </>
  )
}

function OrdersView({ d, f }: { d: OrgPurchaseOrders; f: F }) {
  const { t } = useLanguage()
  const row = (name: string, r: OrgPurchaseOrders['totals'], bold = false) => {
    const w = (n: number) => (bold ? <strong>{f.num(n)}</strong> : f.num(n))
    return (
      <>
        <Td>{bold ? <strong>{name}</strong> : name}</Td>
        <Td align="right" mono>{w(r.orders)}</Td><Td align="right" mono>{w(r.cancelled)}</Td>
        <Td align="right" mono>{w(r.not_sent)}</Td><Td align="right" mono>{w(r.sent)}</Td>
        <Td align="right" mono>{w(r.awaiting_payment)}</Td><Td align="right" mono>{w(r.awaiting_reception)}</Td>
        <Td align="right" mono>{f.money(r.value)}</Td>
      </>
    )
  }
  return (
    <>
      <p style={{ margin: '0 0 8px', fontSize: 12, color: C.muted }}>{t('org.orders_hint')}</p>
      <Table minWidth={760}>
        <thead><tr>
          <Th>{t('org.col_company')}</Th><Th align="right">{t('org.col_orders')}</Th>
          <Th align="right">{t('org.col_cancelled')}</Th><Th align="right">{t('org.col_not_sent')}</Th>
          <Th align="right">{t('org.col_sent')}</Th><Th align="right">{t('org.col_awaiting_payment')}</Th>
          <Th align="right">{t('org.col_awaiting_reception')}</Th><Th align="right">{t('org.col_order_value')}</Th>
        </tr></thead>
        <tbody>
          {d.tenants.map(r => <Tr key={r.tenant_id}>{row(r.name, r as unknown as OrgPurchaseOrders['totals'])}</Tr>)}
          <Tr>{row(t('org.col_total'), d.totals, true)}</Tr>
        </tbody>
      </Table>
      {d.totals.orders_without_value > 0 && <Note>{t('org.orders_without_value', { count: d.totals.orders_without_value })}</Note>}
      <Note>{t('org.money_per_currency')}</Note>
      <UnavailableNote items={d.unavailable} />
    </>
  )
}

function BudgetsView({ d, f }: { d: OrgBudgets; f: F }) {
  const { t } = useLanguage()
  return (
    <>
      <p style={{ margin: '0 0 8px', fontSize: 12, color: C.muted }}>{t('org.budgets_hint')}</p>
      <Table minWidth={760}>
        <thead><tr>
          <Th>{t('org.col_company')}</Th><Th>{t('org.col_period')}</Th><Th align="right">{t('org.col_budget')}</Th>
          <Th align="right">{t('org.col_spent')}</Th><Th align="right">{t('org.col_committed')}</Th>
          <Th align="right">{t('org.col_remaining')}</Th>
        </tr></thead>
        <tbody>
          {d.tenants.flatMap(r => r.budgets.length === 0
            ? [<Tr key={r.tenant_id}><Td>{r.name}</Td><Td colSpan={5} style={{ color: C.dim }}>{t('org.budget_none')}</Td></Tr>]
            : r.budgets.map(b => (
              <Tr key={`${r.tenant_id}:${b.root_id}`}>
                <Td>{r.name}</Td>
                <Td nowrap>{f.date(b.period_start)} – {f.date(b.period_end)}</Td>
                <Td align="right" mono>{f.one(b.amount, b.currency)}</Td>
                <Td align="right" mono>{f.one(b.spent, b.currency)}</Td>
                <Td align="right" mono>{f.one(b.committed, b.currency)}</Td>
                <Td align="right" mono style={{ color: b.over_budget ? C.red : undefined }}>
                  {f.one(b.remaining, b.currency)}{b.over_budget ? ` · ${t('org.budget_over')}` : ''}
                  {b.currency_mismatch && <div style={{ fontSize: 11, color: C.red }}>{t('org.budget_mismatch')}</div>}
                </Td>
              </Tr>
            )))}
          {d.totals.by_currency.map(c => (
            <Tr key={c.currency}>
              <Td><strong>{t('org.col_total')}</strong></Td><Td>{c.currency}</Td>
              <Td align="right" mono><strong>{f.one(c.amount, c.currency)}</strong></Td>
              <Td align="right" mono><strong>{f.one(c.spent, c.currency)}</strong></Td>
              <Td align="right" mono><strong>{f.one(c.committed, c.currency)}</strong></Td>
              <Td align="right" mono><strong>{f.one(c.remaining, c.currency)}</strong></Td>
            </Tr>
          ))}
        </tbody>
      </Table>
      {d.totals.unknown_cost_lines > 0 && <Note>{t('org.budget_unknown_cost', { count: d.totals.unknown_cost_lines })}</Note>}
      {d.tenants.filter(r => r.other_scope_budgets_not_included > 0).map(r => (
        <Note key={r.tenant_id}>{r.name}: {t('org.budget_other_scopes', { count: r.other_scope_budgets_not_included })}</Note>
      ))}
      <Note>{t('org.money_per_currency')}</Note>
      <UnavailableNote items={d.unavailable} />
    </>
  )
}

// ── Holding administration ───────────────────────────────────────────────────

function Grants({ link, people, onChanged }: { link: OrgLinkAsParent; people: AdminUser[]; onChanged: () => void }) {
  const { t } = useLanguage()
  const errorDetail = useErrorDetail()
  const [members, setMembers] = useState<OrgMember[] | null>(null)
  const [pick, setPick] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)

  const load = useCallback(() => {
    listOrgLinkMembers(link.id).then(setMembers).catch(e => setError(errorDetail(e)))
  }, [link.id, errorDetail])
  useEffect(load, [load])

  async function run(fn: () => Promise<unknown>) {
    setBusy(true); setError(null)
    try { await fn(); load(); onChanged() } catch (e) { setError(errorDetail(e)) } finally { setBusy(false) }
  }

  const granted = new Set((members ?? []).map(m => m.user_id))
  const candidates = people.filter(p => !granted.has(p.id))
  return (
    <div style={{ marginTop: 10 }}>
      <h4 style={{ margin: '0 0 6px', fontSize: 12.5, fontWeight: 700 }}>{t('org.grants_title')}</h4>
      {members && members.length === 0 && <p style={{ margin: '0 0 8px', fontSize: 12, color: C.muted }}>{t('org.grants_none')}</p>}
      <ul style={{ listStyle: 'none', margin: 0, padding: 0, display: 'flex', flexDirection: 'column', gap: 6 }}>
        {(members ?? []).map(m => (
          <li key={m.user_id} style={{ display: 'flex', gap: 8, alignItems: 'center', flexWrap: 'wrap', fontSize: 12.5 }}>
            <span style={{ overflowWrap: 'anywhere' }}>{m.full_name || m.email}</span>
            <span style={{ color: C.dim }}>{m.email}</span>
            <button type="button" style={btn} disabled={busy} onClick={() => run(() => removeOrgLinkMember(link.id, m.user_id))}>
              {t('org.grant_remove')}
            </button>
          </li>
        ))}
      </ul>
      <div style={{ display: 'flex', gap: 8, marginTop: 8, flexWrap: 'wrap', alignItems: 'center' }}>
        <div style={{ minWidth: 220, flex: '1 1 220px' }}>
          <Select value={pick} onChange={e => setPick(e.target.value)} aria-label={t('org.grant_pick')}>
            <option value="">{t('org.grant_pick')}</option>
            {candidates.map(p => <option key={p.id} value={p.id}>{p.full_name || p.email}</option>)}
          </Select>
        </div>
        <button type="button" style={{ ...btn, opacity: pick && !busy ? 1 : 0.5 }} disabled={!pick || busy}
                onClick={() => run(async () => { await grantOrgLinkMember(link.id, pick); setPick('') })}>
          {t('org.grant_add')}
        </button>
      </div>
      {error && <Note tone="warn">{error}</Note>}
    </div>
  )
}

function Manage({ links, reload }: { links: OrgLinks; reload: () => void }) {
  const { t } = useLanguage()
  const errorDetail = useErrorDetail()
  const confirm = useConfirm()
  const f = useFormatters()
  const [label, setLabel] = useState('')
  const [created, setCreated] = useState<OrgCreatedLink | null>(null)
  const [copied, setCopied] = useState(false)
  const [people, setPeople] = useState<AdminUser[]>([])
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)

  useEffect(() => {
    listAdminUsers({ limit: 200 }).then(r => setPeople(
      (r.items ?? []).filter((u: AdminUser) => u.status === 'active'))).catch(() => {})
  }, [])

  async function run(fn: () => Promise<unknown>) {
    setBusy(true); setError(null)
    try { await fn(); reload() } catch (e) { setError(errorDetail(e)) } finally { setBusy(false) }
  }

  return (
    <Card padding={16}>
      <h2 style={{ margin: '0 0 4px', fontSize: 14, fontWeight: 700 }}>{t('org.manage_title')}</h2>
      <p style={{ margin: '0 0 12px', fontSize: 12.5, color: C.muted }}>{t('org.manage_hint')}</p>
      {error && <p role="alert" style={{ margin: '0 0 8px', fontSize: 12.5, color: C.red }}>{error}</p>}

      <div style={{ display: 'flex', gap: 10, flexWrap: 'wrap', alignItems: 'flex-end' }}>
        <div style={{ flex: '1 1 240px', minWidth: 0 }}>
          <Field label={t('org.link_label')} hint={t('org.link_label_hint')}>
            <Input value={label} maxLength={80} onChange={e => setLabel(e.target.value)} />
          </Field>
        </div>
        <button type="button" style={{ ...btn, opacity: label.trim() && !busy ? 1 : 0.5 }} disabled={!label.trim() || busy}
                onClick={() => run(async () => { setCreated(await createOrgLink(label.trim())); setLabel(''); setCopied(false) })}>
          {t('org.link_create')}
        </button>
      </div>

      {created && (
        <div style={{ marginTop: 12, border: `1px solid var(--accent)`, borderRadius: 10, padding: 12 }}>
          <div style={{ fontSize: 12.5, fontWeight: 700 }}>{t('org.code_title')} · {created.label}</div>
          <code style={{ display: 'block', margin: '8px 0', fontSize: 13, overflowWrap: 'anywhere' }}>{created.code}</code>
          <div style={{ display: 'flex', gap: 8, alignItems: 'center', flexWrap: 'wrap' }}>
            <button type="button" style={btn}
                    onClick={() => { void navigator.clipboard?.writeText(created.code).then(() => setCopied(true)).catch(() => {}) }}>
              {copied ? t('org.code_copied') : t('org.code_copy')}
            </button>
            <span style={{ fontSize: 12, color: C.muted }}>{t('org.code_once', { date: f.date(created.expires_at) })}</span>
          </div>
        </div>
      )}

      {links.as_parent.length === 0 && <p style={{ margin: '16px 0 0', fontSize: 12.5, color: C.muted }}>{t('org.links_empty')}</p>}
      <ul style={{ listStyle: 'none', margin: '16px 0 0', padding: 0, display: 'flex', flexDirection: 'column', gap: 10 }}>
        {links.as_parent.map(l => (
          <li key={l.id} style={{ border: `1px solid ${C.border}`, borderRadius: 10, padding: 12,
                                  background: 'var(--surface-2)', opacity: l.status === 'revoked' ? 0.6 : 1 }}>
            <div style={{ display: 'flex', gap: 8, alignItems: 'center', flexWrap: 'wrap', justifyContent: 'space-between' }}>
              <div style={{ minWidth: 0 }}>
                <div style={{ fontSize: 13, fontWeight: 600, overflowWrap: 'anywhere' }}>{l.label}</div>
                <div style={{ fontSize: 12, color: C.muted }}>
                  {l.status === 'pending' && l.expired ? t('org.expired') : t(`org.status.${l.status}`)}
                  {l.status === 'active' && l.subsidiary_name ? ` · ${t('org.subsidiary', { name: l.subsidiary_name })}` : ''}
                  {l.status === 'revoked' && l.revoked_side
                    ? ` · ${l.revoked_side === 'child' ? t('org.revoked_by_child') : t('org.revoked_by_parent')}` : ''}
                </div>
              </div>
              {l.status !== 'revoked' && (
                <button type="button" style={btn} disabled={busy} onClick={async () => {
                  if (await confirm({ title: t('org.link_revoke_title'), message: t('org.link_revoke_body_parent'),
                                      confirmLabel: t('org.link_revoke'), danger: true })) {
                    void run(() => revokeOrgLink(l.id))
                  }
                }}>{t('org.link_revoke')}</button>
              )}
            </div>
            {l.status === 'active' && <Grants link={l} people={people} onChanged={reload} />}
          </li>
        ))}
      </ul>
    </Card>
  )
}

// ── Subsidiary side ──────────────────────────────────────────────────────────

function Join({ links, reload }: { links: OrgLinks; reload: () => void }) {
  const { t } = useLanguage()
  const errorDetail = useErrorDetail()
  const confirm = useConfirm()
  const f = useFormatters()
  const [code, setCode] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [done, setDone] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const active = links.as_child.find(l => l.status === 'active')
  const past = links.as_child.filter(l => l.status !== 'active')

  async function run(fn: () => Promise<unknown>) {
    setBusy(true); setError(null)
    try { await fn(); reload() } catch (e) { setError(errorDetail(e)) } finally { setBusy(false) }
  }

  return (
    <Card padding={16}>
      <h2 style={{ margin: '0 0 4px', fontSize: 14, fontWeight: 700 }}>{active ? t('org.as_child_title') : t('org.join_title')}</h2>
      {error && <p role="alert" style={{ margin: '0 0 8px', fontSize: 12.5, color: C.red }}>{error}</p>}
      {active ? (
        <>
          <p style={{ margin: '0 0 10px', fontSize: 12.5, color: C.muted, lineHeight: 1.5 }}>
            {t('org.as_child_linked', { name: active.parent_name ?? '' })}
          </p>
          <button type="button" style={btn} disabled={busy} onClick={async () => {
            if (await confirm({ title: t('org.link_revoke_title'), message: t('org.link_revoke_body_child'),
                                confirmLabel: t('org.link_revoke'), danger: true })) {
              void run(() => revokeOrgLink(active.id))
            }
          }}>{t('org.link_revoke')}</button>
        </>
      ) : (
        <>
          <p style={{ margin: '0 0 10px', fontSize: 12.5, color: C.muted, lineHeight: 1.5 }}>{t('org.join_hint')}</p>
          <div style={{ display: 'flex', gap: 10, flexWrap: 'wrap', alignItems: 'flex-end' }}>
            <div style={{ flex: '1 1 280px', minWidth: 0 }}>
              <Field label={t('org.join_code')}>
                <Input value={code} autoComplete="off" spellCheck={false} onChange={e => setCode(e.target.value)} />
              </Field>
            </div>
            <button type="button" style={{ ...btn, opacity: code.trim() && !busy ? 1 : 0.5 }} disabled={!code.trim() || busy}
                    onClick={() => run(async () => {
                      const r = await acceptOrgLink(code.trim())
                      setDone(t('org.joined', { name: r.parent_name })); setCode('')
                    })}>
              {t('org.join_btn')}
            </button>
          </div>
          {done && <Note>{done}</Note>}
        </>
      )}
      {past.length > 0 && (
        <div style={{ marginTop: 12 }}>
          <h3 style={{ margin: '0 0 4px', fontSize: 12.5, fontWeight: 700, color: C.muted }}>{t('org.as_child_history')}</h3>
          <ul style={{ margin: 0, paddingLeft: 18, fontSize: 12.5, color: C.muted }}>
            {past.map(l => (
              <li key={l.id}>
                {f.date(l.accepted_at)} – {f.date(l.revoked_at)} · {l.revoked_side === 'parent' ? t('org.revoked_by_parent') : t('org.revoked_by_child')}
              </li>
            ))}
          </ul>
        </div>
      )}
    </Card>
  )
}

// ── The page ─────────────────────────────────────────────────────────────────

export default function OrganizationPage() {
  const { t } = useLanguage()
  const errorDetail = useErrorDetail()
  const [overview, setOverview] = useState<OrgOverview | null>(null)
  const [links, setLinks] = useState<OrgLinks | null>(null)
  const [error, setError] = useState<string | null>(null)

  const load = useCallback(() => {
    setError(null)
    getOrgOverview().then(o => {
      setOverview(o)
      if (o.can_manage) listOrgLinks().then(setLinks).catch(e => setError(errorDetail(e)))
      else setLinks(null)
    }).catch(e => setError(errorDetail(e)))
  }, [errorDetail])
  useEffect(load, [load])

  const noOrg = useMemo(() => overview && !overview.entitled && !(links && (links.as_parent.length || links.as_child.length)), [overview, links])

  if (!overview) {
    return error ? <p role="alert" style={{ color: C.red, fontSize: 13 }}>{error}</p> : <LoadingState label={t('common.loading')} />
  }
  return (
    <div style={{ width: '100%', maxWidth: 960, margin: '0 auto', display: 'flex', flexDirection: 'column', gap: 20 }}>
      <p style={{ margin: 0, fontSize: 13, color: C.muted, lineHeight: 1.5 }}>{t('org.intro')}</p>
      {error && <p role="alert" style={{ margin: 0, fontSize: 12.5, color: C.red }}>{error}</p>}
      {overview.entitled && <Consolidated overview={overview} />}
      {overview.can_manage && links && <Manage links={links} reload={load} />}
      {overview.can_manage && links && <Join links={links} reload={load} />}
      {noOrg && (
        <EmptyState title={t('org.none_title')} body={overview.can_manage ? t('org.none_body') : t('org.admin_only_hint')} compact />
      )}
    </div>
  )
}
