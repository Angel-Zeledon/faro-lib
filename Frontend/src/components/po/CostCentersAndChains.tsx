'use client'
/**
 * Cost centers and approval chains: the two cards under the approval rules on
 * /aprobaciones (admins only; the page already refuses anyone else).
 *
 *  - Cost centers: a tree of places money is spent from. An order is charged to
 *    one when it is created (and a budget can be set on one, in the budget panel).
 *  - Approval chains: from an amount up, an order needs one or more LEVELS of
 *    approval, in order, each a role or named people. A chain belongs to one
 *    cost center (and the centers below it) or is the default for every other
 *    order.
 *
 * Off until the first chain exists. Served by the Rust API only: with it down
 * these calls answer 404 and the card says so. Nothing is ever deleted: a
 * center or a chain is paused, because orders and approvals keep pointing at it.
 */
import { useCallback, useEffect, useState } from 'react'
import {
  createApprovalChain, createCostCenter, listApprovalChains, listCostCenters,
  updateApprovalChain, updateCostCenter,
} from '@/lib/api'
import type { ApprovalChain, ChainCandidate, ChainLevel, CostCenter } from '@/lib/types'
import Card from '@/components/ui/Card'
import Input, { Field, Select } from '@/components/ui/Input'
import { useErrorDetail } from '@/components/ui/States'
import { useLanguage } from '@/contexts/LanguageContext'
import { formatMoney } from '@/lib/currency'

const C = { border: 'var(--border)', text: 'var(--text)', muted: 'var(--muted)', dim: 'var(--dim)', red: '#C0504D' }

const btn: React.CSSProperties = {
  all: 'unset', cursor: 'pointer', display: 'inline-flex', alignItems: 'center', gap: 4,
  padding: '6px 12px', borderRadius: 8, fontSize: 12, fontWeight: 600,
  border: `1px solid ${C.border}`, color: C.text,
}

type LevelDraft = { kind: 'admin' | 'analyst' | 'users'; user_ids: string[] }
type BandDraft = { min: string; levels: LevelDraft[] }
type Draft = { id: string | null; name: string; center: string; bands: BandDraft[] }

const EMPTY_DRAFT = (): Draft => ({ id: null, name: '', center: '', bands: [{ min: '', levels: [{ kind: 'analyst', user_ids: [] }] }] })

function levelFromDraft(l: LevelDraft): ChainLevel {
  return l.kind === 'users' ? { kind: 'users', user_ids: l.user_ids } : { kind: 'role', role: l.kind }
}

function draftFromChain(c: ApprovalChain): Draft {
  return {
    id: c.id, name: c.name, center: c.cost_center_id ?? '',
    bands: c.bands.map(b => ({
      min: String(b.min_amount),
      levels: b.levels.map(l => l.kind === 'users'
        ? { kind: 'users' as const, user_ids: l.user_ids }
        : { kind: l.role, user_ids: [] }),
    })),
  }
}

export default function CostCentersAndChains() {
  const { t } = useLanguage()
  const errorDetail = useErrorDetail()
  const [centers, setCenters] = useState<CostCenter[] | null>(null)
  const [chains, setChains] = useState<ApprovalChain[]>([])
  const [people, setPeople] = useState<ChainCandidate[]>([])
  const [maxLevels, setMaxLevels] = useState(5)
  const [maxBands, setMaxBands] = useState(10)
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const [form, setForm] = useState({ code: '', name: '', parent: '' })
  const [draft, setDraft] = useState<Draft | null>(null)

  const load = useCallback(() => {
    Promise.all([listCostCenters({ silent: true }), listApprovalChains({ silent: true })])
      .then(([c, ch]) => {
        setCenters(c.items); setChains(ch.items); setPeople(ch.candidates)
        setMaxLevels(ch.max_levels); setMaxBands(ch.max_bands)
      })
      .catch(e => { setError(errorDetail(e)); setCenters(prev => prev ?? []) })
  }, [errorDetail])
  useEffect(() => { load() }, [load])

  async function run(fn: () => Promise<unknown>) {
    setBusy(true); setError(null)
    try { await fn(); load() } catch (e: unknown) { setError(errorDetail(e)) } finally { setBusy(false) }
  }

  const centerList = centers ?? []
  const activeCenters = centerList.filter(c => c.active)
  const pathOf = (id: string | null) => centerList.find(c => c.id === id)?.path ?? ''
  const activeChains = chains.filter(c => c.active)
  const hasDefault = activeChains.some(c => c.cost_center_id === null)
  const nameOf = (id: string) => people.find(p => p.id === id)?.name ?? id

  function levelText(l: ChainLevel): string {
    if (l.kind === 'role') return t(`cc.role_${l.role}`)
    return (l.users?.map(u => u.name ?? u.id) ?? l.user_ids.map(nameOf)).join(', ')
  }

  function draftValid(d: Draft): boolean {
    if (!d.name.trim() || d.bands.length === 0) return false
    const mins = d.bands.map(b => b.min.trim())
    if (mins.some(m => m === '' || !(Number(m) >= 0)) || new Set(mins.map(Number)).size !== mins.length) return false
    return d.bands.every(b => b.levels.length > 0 && b.levels.every(l => l.kind !== 'users' || l.user_ids.length > 0))
  }

  function saveDraft(d: Draft) {
    const bands = d.bands.map(b => ({ min_amount: Number(b.min), levels: b.levels.map(levelFromDraft) }))
    return run(async () => {
      if (d.id) await updateApprovalChain(d.id, { name: d.name.trim(), bands })
      else await createApprovalChain({ name: d.name.trim(), cost_center_id: d.center || null, bands })
      setDraft(null)
    })
  }

  const patchBand = (i: number, fn: (b: BandDraft) => BandDraft) =>
    setDraft(d => d && { ...d, bands: d.bands.map((b, j) => (j === i ? fn(b) : b)) })
  const patchLevel = (i: number, k: number, fn: (l: LevelDraft) => LevelDraft) =>
    patchBand(i, b => ({ ...b, levels: b.levels.map((l, j) => (j === k ? fn(l) : l)) }))

  if (centers === null) return null

  return (
    <>
      {error && <p role="alert" style={{ margin: 0, fontSize: 12.5, color: C.red }}>{error}</p>}

      <Card padding={16}>
        <h2 style={{ margin: '0 0 4px', fontSize: 14, fontWeight: 700 }}>{t('cc.centers_title')}</h2>
        <p style={{ margin: '0 0 12px', fontSize: 12.5, color: C.muted }}>{t('cc.centers_hint')}</p>
        {centerList.length === 0 && <p style={{ margin: '0 0 12px', fontSize: 13, color: C.dim }}>{t('cc.centers_empty')}</p>}
        {centerList.length > 0 && (
          <ul style={{ listStyle: 'none', margin: '0 0 16px', padding: 0, display: 'flex', flexDirection: 'column', gap: 6 }}>
            {centerList.map(c => (
              <li key={c.id} style={{
                display: 'flex', flexWrap: 'wrap', alignItems: 'center', gap: 8, justifyContent: 'space-between',
                paddingLeft: (c.depth ?? 0) * 18, opacity: c.active ? 1 : 0.6,
              }}>
                <span style={{ fontSize: 13, overflowWrap: 'anywhere' }}>
                  <strong>{c.code}</strong> {c.name}
                  {!c.active && <span style={{ color: C.dim }}> · {t('cc.inactive')}</span>}
                  {c.chain_id && <span style={{ color: C.muted }}> · {t('cc.has_chain')}</span>}
                </span>
                <button type="button" style={btn} disabled={busy}
                        onClick={() => run(() => updateCostCenter(c.id, { active: !c.active }))}>
                  {c.active ? t('cc.pause') : t('cc.resume')}
                </button>
              </li>
            ))}
          </ul>
        )}
        <h3 style={{ margin: '0 0 8px', fontSize: 12.5, fontWeight: 700, color: C.muted }}>{t('cc.center_add')}</h3>
        <div style={{ display: 'grid', gap: 10, gridTemplateColumns: 'repeat(auto-fit, minmax(180px, 1fr))' }}>
          <Field label={t('cc.field_code')}>
            <Input maxLength={40} value={form.code} onChange={e => setForm(f => ({ ...f, code: e.target.value }))} />
          </Field>
          <Field label={t('cc.field_name')}>
            <Input maxLength={120} value={form.name} onChange={e => setForm(f => ({ ...f, name: e.target.value }))} />
          </Field>
          <Field label={t('cc.field_parent')}>
            <Select value={form.parent} onChange={e => setForm(f => ({ ...f, parent: e.target.value }))}>
              <option value="">{t('cc.parent_none')}</option>
              {activeCenters.map(c => <option key={c.id} value={c.id}>{c.path ?? c.code}</option>)}
            </Select>
          </Field>
        </div>
        <div style={{ marginTop: 12 }}>
          <button type="button" style={{ ...btn, opacity: form.code.trim() && form.name.trim() && !busy ? 1 : 0.5 }}
                  disabled={!form.code.trim() || !form.name.trim() || busy}
                  onClick={() => run(async () => {
                    await createCostCenter({ code: form.code.trim(), name: form.name.trim(), parent_id: form.parent || null })
                    setForm({ code: '', name: '', parent: '' })
                  })}>
            {t('cc.center_add_btn')}
          </button>
        </div>
      </Card>

      <Card padding={16}>
        <h2 style={{ margin: '0 0 4px', fontSize: 14, fontWeight: 700 }}>{t('cc.chains_title')}</h2>
        <p style={{ margin: '0 0 12px', fontSize: 12.5, color: C.muted }}>{t('cc.chains_hint')}</p>
        {activeChains.length > 0 && !hasDefault && (
          <p role="status" style={{ margin: '0 0 12px', fontSize: 12.5, color: C.red }}>{t('cc.no_default_warning')}</p>
        )}
        {chains.length === 0 && <p style={{ margin: '0 0 12px', fontSize: 13, color: C.dim }}>{t('cc.chains_empty')}</p>}
        <ul style={{ listStyle: 'none', margin: '0 0 16px', padding: 0, display: 'flex', flexDirection: 'column', gap: 8 }}>
          {chains.map(c => (
            <li key={c.id} style={{
              border: `1px solid ${C.border}`, borderRadius: 10, padding: 12, background: 'var(--surface-2)',
              opacity: c.active ? 1 : 0.6,
            }}>
              <div style={{ fontSize: 13, fontWeight: 600, overflowWrap: 'anywhere' }}>
                {c.name}
                {!c.active && <span style={{ color: C.dim, fontWeight: 400 }}> · {t('cc.inactive')}</span>}
              </div>
              <div style={{ fontSize: 12, color: C.muted, marginTop: 2 }}>
                {c.cost_center_id ? t('cc.scope_center', { name: pathOf(c.cost_center_id) || `${c.cost_center_code ?? ''}` }) : t('cc.scope_default')}
              </div>
              {c.bands.map(b => (
                <div key={b.min_amount} style={{ fontSize: 12.5, marginTop: 6 }}>
                  <strong>{t('cc.band_from', { amount: formatMoney(b.min_amount) })}</strong>
                  {b.levels.map((l, i) => (
                    <div key={i} style={{ color: C.muted, paddingLeft: 10 }}>
                      {t('cc.level_line', { n: i + 1, who: levelText(l) })}
                    </div>
                  ))}
                </div>
              ))}
              <div style={{ display: 'flex', gap: 8, marginTop: 10 }}>
                <button type="button" style={btn} disabled={busy} onClick={() => setDraft(draftFromChain(c))}>
                  {t('cc.chain_edit')}
                </button>
                <button type="button" style={btn} disabled={busy}
                        onClick={() => run(() => updateApprovalChain(c.id, { active: !c.active }))}>
                  {c.active ? t('cc.pause') : t('cc.resume')}
                </button>
              </div>
            </li>
          ))}
        </ul>

        {draft === null ? (
          <button type="button" style={btn} disabled={busy} onClick={() => setDraft(EMPTY_DRAFT())}>
            {t('cc.chain_add')}
          </button>
        ) : (
          <div style={{ border: `1px solid ${C.border}`, borderRadius: 10, padding: 12, display: 'flex', flexDirection: 'column', gap: 12 }}>
            <h3 style={{ margin: 0, fontSize: 12.5, fontWeight: 700, color: C.muted }}>
              {draft.id ? t('cc.chain_edit') : t('cc.chain_add')}
            </h3>
            <div style={{ display: 'grid', gap: 10, gridTemplateColumns: 'repeat(auto-fit, minmax(200px, 1fr))' }}>
              <Field label={t('cc.field_chain_name')}>
                <Input maxLength={120} value={draft.name} onChange={e => setDraft(d => d && { ...d, name: e.target.value })} />
              </Field>
              {!draft.id && (
                <Field label={t('cc.field_chain_center')} hint={t('cc.field_chain_center_hint')}>
                  <Select value={draft.center} onChange={e => setDraft(d => d && { ...d, center: e.target.value })}>
                    <option value="">{t('cc.scope_default')}</option>
                    {activeCenters.map(c => <option key={c.id} value={c.id}>{c.path ?? c.code}</option>)}
                  </Select>
                </Field>
              )}
            </div>
            {draft.bands.map((b, i) => (
              <fieldset key={i} style={{ border: `1px solid ${C.border}`, borderRadius: 8, padding: 10, margin: 0 }}>
                <legend style={{ fontSize: 12, color: C.muted, padding: '0 6px' }}>{t('cc.band_n', { n: i + 1 })}</legend>
                <Field label={t('cc.field_band_min')}>
                  <Input type="number" min={0} inputMode="decimal" value={b.min}
                         onChange={e => patchBand(i, x => ({ ...x, min: e.target.value }))} />
                </Field>
                {b.levels.map((l, k) => (
                  <div key={k} style={{ marginTop: 10, display: 'flex', flexDirection: 'column', gap: 6 }}>
                    <div style={{ display: 'flex', gap: 8, alignItems: 'center', flexWrap: 'wrap' }}>
                      <strong style={{ fontSize: 12.5 }}>{t('cc.level_n', { n: k + 1 })}</strong>
                      <Select value={l.kind} aria-label={t('cc.level_kind')}
                              onChange={e => patchLevel(i, k, x => ({ ...x, kind: e.target.value as LevelDraft['kind'] }))}>
                        <option value="analyst">{t('cc.role_analyst')}</option>
                        <option value="admin">{t('cc.role_admin')}</option>
                        <option value="users">{t('cc.level_named')}</option>
                      </Select>
                      {b.levels.length > 1 && (
                        <button type="button" style={btn} onClick={() => patchBand(i, x => ({ ...x, levels: x.levels.filter((_, j) => j !== k) }))}>
                          {t('cc.level_remove')}
                        </button>
                      )}
                    </div>
                    {l.kind === 'users' && (
                      <div style={{ display: 'flex', flexWrap: 'wrap', gap: 10 }}>
                        {people.map(p => (
                          <label key={p.id} style={{ display: 'flex', gap: 6, alignItems: 'center', fontSize: 12.5 }}>
                            <input type="checkbox" checked={l.user_ids.includes(p.id)}
                                   onChange={e => patchLevel(i, k, x => ({
                                     ...x, user_ids: e.target.checked ? [...x.user_ids, p.id] : x.user_ids.filter(u => u !== p.id),
                                   }))} />
                            {p.name}
                          </label>
                        ))}
                      </div>
                    )}
                  </div>
                ))}
                <div style={{ display: 'flex', gap: 8, marginTop: 10, flexWrap: 'wrap' }}>
                  {b.levels.length < maxLevels && (
                    <button type="button" style={btn}
                            onClick={() => patchBand(i, x => ({ ...x, levels: [...x.levels, { kind: 'admin', user_ids: [] }] }))}>
                      {t('cc.level_add')}
                    </button>
                  )}
                  {draft.bands.length > 1 && (
                    <button type="button" style={btn}
                            onClick={() => setDraft(d => d && { ...d, bands: d.bands.filter((_, j) => j !== i) })}>
                      {t('cc.band_remove')}
                    </button>
                  )}
                </div>
              </fieldset>
            ))}
            <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap' }}>
              {draft.bands.length < maxBands && (
                <button type="button" style={btn}
                        onClick={() => setDraft(d => d && { ...d, bands: [...d.bands, { min: '', levels: [{ kind: 'admin', user_ids: [] }] }] })}>
                  {t('cc.band_add')}
                </button>
              )}
              <button type="button" style={{ ...btn, opacity: draftValid(draft) && !busy ? 1 : 0.5 }}
                      disabled={!draftValid(draft) || busy} onClick={() => saveDraft(draft)}>
                {t('cc.chain_save')}
              </button>
              <button type="button" style={btn} onClick={() => setDraft(null)}>{t('common.cancel')}</button>
            </div>
          </div>
        )}
      </Card>
    </>
  )
}
