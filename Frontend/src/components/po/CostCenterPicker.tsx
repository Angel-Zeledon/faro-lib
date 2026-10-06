'use client'
/**
 * "Charge this order to…": the cost center an order's spend is attributed to.
 * Renders nothing until the company has at least one active cost center, so a
 * tenant that never set any sees the cart exactly as it was. Centers come from
 * the Rust API; when it is down the list is empty and so is this control.
 */
import { useEffect, useState } from 'react'
import { listCostCenters } from '@/lib/api'
import type { CostCenter } from '@/lib/types'
import { useLanguage } from '@/contexts/LanguageContext'

export function useActiveCostCenters(): CostCenter[] {
  const [centers, setCenters] = useState<CostCenter[]>([])
  useEffect(() => {
    let alive = true
    listCostCenters({ silent: true })
      .then(r => { if (alive) setCenters(r.items.filter(c => c.active)) })
      .catch(() => { if (alive) setCenters([]) })
    return () => { alive = false }
  }, [])
  return centers
}

export default function CostCenterPicker({ centers, value, onChange }: {
  centers: CostCenter[]
  value: string
  onChange: (id: string) => void
}) {
  const { t } = useLanguage()
  if (centers.length === 0) return null
  return (
    <label style={{ display: 'flex', alignItems: 'center', gap: 6, fontSize: 12, color: 'var(--dim)' }}>
      {t('cc.po_center_label')}
      <select
        name="cart_cost_center"
        value={value}
        onChange={e => onChange(e.target.value)}
        style={{
          background: 'var(--surface-2)', border: '1px solid var(--border)',
          borderRadius: 6, padding: '5px 8px', color: 'var(--text)',
          fontSize: 12, fontWeight: 600, outline: 'none', cursor: 'pointer', maxWidth: 220,
        }}
      >
        <option value="">{t('cc.po_center_none')}</option>
        {centers.map(c => <option key={c.id} value={c.id}>{c.path ?? c.code}</option>)}
      </select>
    </label>
  )
}
