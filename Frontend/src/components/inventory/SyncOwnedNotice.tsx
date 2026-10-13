'use client'
/**
 * "This value comes from <sync> and will be overwritten at the next sync."
 *
 * The conflict rule of inventory sync is "the source wins": a quantity, cost,
 * supplier or lead time typed by hand into a field a live sync owns is
 * replaced at that sync's next run (counted and audit-logged). Saving stays
 * allowed — this only makes sure the person knows BEFORE saving, instead of
 * finding their number gone in an hour.
 *
 * `useSyncOwnership(skus)` asks the backend once per set of SKUs;
 * `<SyncOwnedNotice>` renders the sentence for one owner and the fields the
 * person is changing.
 */
import { useEffect, useMemo, useState } from 'react'
import { AlertTriangle } from 'lucide-react'
import { getSyncOwnership, type SyncOwnerProfile } from '@/lib/api'
import { useLanguage } from '@/contexts/LanguageContext'

export interface SkuOwner {
  profile: SyncOwnerProfile
  /** Fields the owning sync writes for this SKU (any of its warehouses). */
  fields: Set<string>
  /** warehouse -> fields, when the editor knows which warehouse it writes. */
  byWarehouse: Map<string, Set<string>>
}

/** sku -> its live owner. Empty while loading, on error or with no sync:
 *  a missing notice must never block an edit. */
export function useSyncOwnership(skus: string[] | null | undefined): Map<string, SkuOwner> {
  const [owners, setOwners] = useState<Map<string, SkuOwner>>(new Map())
  const key = useMemo(() => (skus ?? []).filter(Boolean).slice(0, 1000).sort().join(','), [skus])
  useEffect(() => {
    if (!key) { setOwners(new Map()); return }
    let alive = true
    getSyncOwnership(key.split(','))
      .then(res => {
        if (!alive) return
        const profiles = new Map(res.profiles.map(p => [p.id, p]))
        const out = new Map<string, SkuOwner>()
        for (const it of res.items) {
          const profile = profiles.get(it.profile_id)
          if (!profile) continue
          const cur = out.get(it.sku) ?? { profile, fields: new Set<string>(), byWarehouse: new Map() }
          it.fields.forEach(f => cur.fields.add(f))
          cur.byWarehouse.set(it.warehouse, new Set(it.fields))
          out.set(it.sku, cur)
        }
        setOwners(out)
      })
      .catch(() => { if (alive) setOwners(new Map()) })
    return () => { alive = false }
  }, [key])
  return owners
}

/** The fields among `changed` that the owner (optionally in one warehouse) writes. */
export function ownedAmong(owner: SkuOwner | undefined, changed: string[], warehouse?: string | null): string[] {
  if (!owner) return []
  const set = warehouse ? owner.byWarehouse.get(warehouse) : owner.fields
  return set ? changed.filter(f => set.has(f)) : []
}

export function useWhenText() {
  const { t, lang } = useLanguage()
  return (p: SyncOwnerProfile) => {
    if (!p.next_sync_at) return null
    const at = new Date(p.next_sync_at)
    const mins = Math.round((at.getTime() - Date.now()) / 60000)
    if (mins <= 1) return t('isync.owned_when_soon')
    if (mins < 90) return t('isync.owned_when_minutes', { n: mins })
    return at.toLocaleString(lang === 'en' ? 'en-US' : 'es-CR', { dateStyle: 'short', timeStyle: 'short' })
  }
}

/** The sentence itself, for places that need it as text (a confirm dialog). */
export function useOwnedNoticeText() {
  const { t } = useLanguage()
  const whenText = useWhenText()
  return (profile: SyncOwnerProfile, fields: string[]) => {
    const list = fields.map(f => t(`isync.field.${f}`)).join(', ')
    const when = whenText(profile)
    const key = profile.push ? 'isync.owned_notice_push'
      : profile.freshness === 'on_demand' ? 'isync.owned_notice_on_demand'
      : when ? 'isync.owned_notice' : 'isync.owned_notice_next'
    return t(key, { name: profile.name, fields: list, when: when ?? '' })
  }
}

export default function SyncOwnedNotice({ profile, fields, rows, compact }: {
  profile: SyncOwnerProfile
  /** Fields the person is changing that the sync owns. */
  fields: string[]
  /** For a batch (import, several edited rows): how many rows are affected. */
  rows?: number
  compact?: boolean
}) {
  const { t } = useLanguage()
  const text = useOwnedNoticeText()
  if (fields.length === 0) return null
  return (
    <div role="status" data-testid="sync-owned-notice"
         style={{ display: 'flex', alignItems: 'flex-start', gap: 8, padding: compact ? '8px 10px' : '10px 12px',
                  borderRadius: 8, background: 'var(--surface)', border: '1px solid #b0802f',
                  fontSize: 12.5, lineHeight: 1.5, color: 'var(--text)', minWidth: 0 }}>
      <AlertTriangle size={14} color="#b0802f" aria-hidden="true" style={{ flexShrink: 0, marginTop: 2 }} />
      <span style={{ minWidth: 0, overflowWrap: 'anywhere' }}>
        {rows != null && rows > 0 && <strong>{t('isync.owned_rows', { n: rows })} </strong>}
        {text(profile, fields)}
      </span>
    </div>
  )
}
