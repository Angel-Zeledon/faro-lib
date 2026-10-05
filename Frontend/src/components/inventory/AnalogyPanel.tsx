'use client'
/**
 * Forecast by analogy: "this new product will sell like these". A product with
 * too little history cannot be trained on, so an analyst names up to five
 * reference products and a factor, and the purchase recommendation plans from
 * their forecast until the product has a trained model of its own.
 *
 * The estimate is the person's, not a model's: the rows it feeds are marked "By
 * analogy" and low confidence. Entries are append-only on the server (undo
 * stamps, never deletes). Reads are open to everyone signed in; writing is for
 * analysts and admins (the server enforces it, the controls are hidden for
 * viewers). One component serves desktop and phone.
 */
import { useCallback, useEffect, useState } from 'react'
import { GitBranch, Undo2 } from 'lucide-react'
import { createSkuAnalogy, getSkuAnalogies, revertSkuAnalogy } from '@/lib/api'
import type { AnalogyLimits, SkuAnalogy } from '@/lib/types'
import { useErrorDetail } from '@/components/ui/States'
import { useLanguage } from '@/contexts/LanguageContext'
import { useIsNarrow } from '@/hooks/useIsNarrow'
import { getUser } from '@/lib/auth'

const C = { border: 'var(--border)', text: 'var(--text)', muted: 'var(--muted)', dim: 'var(--dim)', red: '#C0504D' }

export const ANALOGY_RELOAD_EVENT = 'stockai:analogy-changed'

export default function AnalogyPanel({ onChanged }: { onChanged?: () => void }) {
  const { t } = useLanguage()
  const errorDetail = useErrorDetail()
  const narrow = useIsNarrow()
  const role = getUser()?.role
  const canWrite = role === 'admin' || role === 'analyst'

  const [items, setItems] = useState<SkuAnalogy[]>([])
  const [limits, setLimits] = useState<AnalogyLimits | null>(null)
  const [loaded, setLoaded] = useState(false)
  const [loadError, setLoadError] = useState<string | null>(null)
  const [open, setOpen] = useState(false)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [form, setForm] = useState({ sku: '', refs: '', scale: '1', start: '', note: '' })

  const load = useCallback(async () => {
    try {
      const r = await getSkuAnalogies()
      setItems(r.items ?? []); setLimits(r.limits ?? null); setLoadError(null)
    } catch (e: unknown) {
      setLoadError(errorDetail(e))
    } finally { setLoaded(true) }
  }, [errorDetail])
  useEffect(() => { void load() }, [load])

  const refs = form.refs.split(',').map(s => s.trim()).filter(Boolean)
  const scale = Number(form.scale.replace(',', '.'))
  const valid = !!form.sku.trim() && refs.length > 0 && Number.isFinite(scale) && scale > 0

  async function save() {
    setBusy(true); setError(null)
    try {
      await createSkuAnalogy({
        new_sku: form.sku.trim(), reference_skus: refs, scale_factor: scale,
        start_date: form.start || undefined, note: form.note.trim() || undefined,
      })
      setOpen(false); setForm({ sku: '', refs: '', scale: '1', start: '', note: '' })
      await load(); onChanged?.()
      window.dispatchEvent(new CustomEvent(ANALOGY_RELOAD_EVENT))
    } catch (e: unknown) {
      setError(errorDetail(e))
    } finally { setBusy(false) }
  }

  async function undo(id: string) {
    setBusy(true); setError(null)
    try {
      await revertSkuAnalogy(id)
      await load(); onChanged?.()
      window.dispatchEvent(new CustomEvent(ANALOGY_RELOAD_EVENT))
    } catch (e: unknown) { setError(errorDetail(e)) }
    finally { setBusy(false) }
  }

  const field: React.CSSProperties = {
    width: '100%', boxSizing: 'border-box', fontSize: narrow ? 14 : 12.5, padding: narrow ? '9px 10px' : '6px 8px',
    borderRadius: 7, border: `1px solid ${C.border}`, background: 'var(--surface-2)', color: C.text,
  }
  const touch = narrow ? 44 : undefined

  // A viewer with nothing to read has nothing to see here.
  if (loaded && !canWrite && items.length === 0 && !loadError) return null

  return (
    <section aria-label={t('analogy.title')} style={{
      border: `1px solid ${C.border}`, borderRadius: 12, background: 'var(--surface)',
      padding: narrow ? '12px 14px' : '14px 20px',
    }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 6 }}>
        <GitBranch size={14} aria-hidden="true" color="var(--accent)" />
        <h3 style={{ margin: 0, fontSize: 13, fontWeight: 600, color: C.text }}>{t('analogy.title')}</h3>
      </div>
      <p style={{ margin: '0 0 10px', fontSize: 12, color: C.dim, lineHeight: 1.5 }}>{t('analogy.intro')}</p>

      {loadError && <p role="alert" style={{ margin: '0 0 8px', fontSize: 12, color: C.red }}>{loadError}</p>}
      {loaded && !loadError && items.length === 0 && (
        <p style={{ margin: '0 0 8px', fontSize: 12, color: C.dim }}>{t('analogy.none')}</p>
      )}
      {items.length > 0 && (
        <ul style={{ listStyle: 'none', margin: '0 0 10px', padding: 0, display: 'flex', flexDirection: 'column', gap: 8 }}>
          {items.map(a => (
            <li key={a.id} style={{ fontSize: 12.5, color: C.text, overflowWrap: 'anywhere', display: 'flex', gap: 8, alignItems: 'baseline', flexWrap: 'wrap' }}>
              <span>
                {t('analogy.line', {
                  name: a.created_by_name || '—', sku: a.new_sku,
                  refs: a.reference_skus.join(', '), factor: String(a.scale_factor),
                })}
                {a.start_date ? t('analogy.line_start', { date: a.start_date }) : ''}
                {a.note ? ` (${a.note})` : ''}
                {a.superseded_at && (
                  <span style={{ color: C.dim }}> · {t('analogy.retired', { date: a.superseded_at.slice(0, 10) })}</span>
                )}
              </span>
              {canWrite && (
                <button type="button" disabled={busy} onClick={() => undo(a.id)} style={{
                  all: 'unset', cursor: busy ? 'default' : 'pointer', display: 'inline-flex', alignItems: 'center', gap: 4,
                  fontSize: 12, fontWeight: 600, color: 'var(--accent)', opacity: busy ? 0.5 : 1,
                  minHeight: touch, padding: narrow ? '0 6px' : undefined,
                }}>
                  <Undo2 size={12} aria-hidden="true" /> {t('analogy.undo')}
                </button>
              )}
            </li>
          ))}
        </ul>
      )}

      {error && !open && <p role="alert" style={{ margin: '0 0 8px', fontSize: 12, color: C.red }}>{error}</p>}
      {canWrite && !open && (
        <button type="button" onClick={() => { setOpen(true); setError(null) }} style={{
          all: 'unset', cursor: 'pointer', display: 'inline-flex', alignItems: 'center', gap: 5,
          padding: narrow ? '0 14px' : '4px 10px', minHeight: touch, borderRadius: 7, fontSize: 12, fontWeight: 600,
          border: `1px solid ${C.border}`, color: C.text,
        }}>
          <GitBranch size={12} aria-hidden="true" /> {t('analogy.add')}
        </button>
      )}
      {canWrite && open && (
        <div style={{ display: 'grid', gap: 8, gridTemplateColumns: narrow ? '1fr' : 'repeat(auto-fit, minmax(170px, 1fr))' }}>
          <label style={{ fontSize: 11.5, color: C.muted }}>{t('analogy.new_sku')}
            <input style={field} type="text" maxLength={200} value={form.sku}
                   onChange={e => setForm(f => ({ ...f, sku: e.target.value }))} />
          </label>
          <label style={{ fontSize: 11.5, color: C.muted }}>{t('analogy.references')}
            <input style={field} type="text" value={form.refs}
                   onChange={e => setForm(f => ({ ...f, refs: e.target.value }))} />
          </label>
          <label style={{ fontSize: 11.5, color: C.muted }}>{t('analogy.scale')}
            <input style={field} type="text" inputMode="decimal" value={form.scale}
                   onChange={e => setForm(f => ({ ...f, scale: e.target.value }))} />
          </label>
          <label style={{ fontSize: 11.5, color: C.muted }}>{t('analogy.start_date')}
            <input style={field} type="date" value={form.start}
                   onChange={e => setForm(f => ({ ...f, start: e.target.value }))} />
          </label>
          <label style={{ fontSize: 11.5, color: C.muted, gridColumn: narrow ? undefined : '1 / -1' }}>{t('analogy.note')}
            <input style={field} type="text" maxLength={300} value={form.note}
                   onChange={e => setForm(f => ({ ...f, note: e.target.value }))} />
          </label>
          {limits && refs.length > limits.max_references && (
            <p role="alert" style={{ gridColumn: '1 / -1', margin: 0, fontSize: 12, color: C.red }}>
              {t('errors.analogy_references_invalid', { min: limits.min_references, max: limits.max_references })}
            </p>
          )}
          {error && <p role="alert" style={{ gridColumn: '1 / -1', margin: 0, fontSize: 12, color: C.red }}>{error}</p>}
          <div style={{ gridColumn: '1 / -1', display: 'flex', gap: 8 }}>
            <button type="button" disabled={busy || !valid} onClick={save} style={{
              all: 'unset', cursor: busy || !valid ? 'default' : 'pointer', padding: narrow ? '0 14px' : '5px 12px',
              minHeight: touch, display: 'inline-flex', alignItems: 'center', borderRadius: 7, fontSize: 12, fontWeight: 600,
              border: '1px solid var(--accent)', background: 'var(--accent)', color: '#fff', opacity: busy || !valid ? 0.5 : 1,
            }}>{busy ? t('common.saving') : t('analogy.save')}</button>
            <button type="button" onClick={() => setOpen(false)} style={{
              all: 'unset', cursor: 'pointer', padding: narrow ? '0 14px' : '5px 12px', minHeight: touch,
              display: 'inline-flex', alignItems: 'center', borderRadius: 7, fontSize: 12, color: C.muted,
            }}>{t('common.cancel')}</button>
          </div>
        </div>
      )}
      <p style={{ margin: '8px 0 0', fontSize: 11, color: C.dim }}>{t('analogy.hint')}</p>
    </section>
  )
}
