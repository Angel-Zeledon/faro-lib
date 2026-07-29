'use client'
/**
 * Which currency the company's own figures are shown in.
 *
 * Not the same thing as what Faro costs: the plans are priced in USD for
 * everyone, and the billing panel above says so. This is for inventory value,
 * unit costs and purchase-order totals — the customer's money.
 *
 * It relabels, it does not convert. Faro stores the numbers it was given and
 * never guesses an exchange rate, so a tenant that already loaded costs in
 * colones and switches to dollars now has dollar-signed colones. The copy says
 * that plainly rather than letting someone find out from a report.
 */
import { useCallback, useEffect, useState } from 'react'
import { Coins, AlertTriangle } from 'lucide-react'

import Spinner from '@/components/ui/Spinner'
import { getTenantCurrency, setTenantCurrency } from '@/lib/api'
import { setActiveCurrency, formatMoney, type CurrencyInfo } from '@/lib/currency'
import { getUser } from '@/lib/auth'
import { useLanguage } from '@/contexts/LanguageContext'
import { useToast } from '@/contexts/ToastContext'

export default function CurrencySection() {
  const { t } = useLanguage()
  const toast = useToast()
  const isAdmin = getUser()?.role === 'admin'

  const [current, setCurrent] = useState<CurrencyInfo | null>(null)
  const [supported, setSupported] = useState<CurrencyInfo[]>([])
  const [loading, setLoading] = useState(true)
  const [saving, setSaving] = useState(false)

  const load = useCallback(() => {
    getTenantCurrency({ silent: true })
      .then(d => {
        setCurrent(d.current)
        setSupported(d.supported || [])
        setActiveCurrency(d.current)
      })
      .catch(() => setCurrent(null))
      .finally(() => setLoading(false))
  }, [])

  useEffect(load, [load])

  const change = async (code: string) => {
    if (!code || code === current?.code) return
    setSaving(true)
    try {
      const { current: next } = await setTenantCurrency(code)
      setCurrent(next)
      setActiveCurrency(next)
      // Every figure already on screen was formatted with the old symbol, and
      // they are spread across screens this component cannot re-render. A reload
      // is the honest way to make the change complete rather than partial.
      toast.addToast(t('currency.section_title'), t('currency.changed_reloading'), 'success')
      window.setTimeout(() => window.location.reload(), 1200)
    } catch {
      /* interceptor surfaced it */
    } finally {
      setSaving(false)
    }
  }

  if (loading) return <div style={{ padding: 8 }}><Spinner size={14} /></div>
  if (!current) return null

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
      <div style={{
        display: 'flex', alignItems: 'center', gap: 9,
        padding: '10px 12px', borderRadius: 9,
        background: 'var(--surface-2)', border: '1px solid var(--border)',
      }}>
        <Coins size={15} color="var(--muted)" aria-hidden="true" />
        <span style={{ fontSize: 13, fontWeight: 600, color: 'var(--text)' }}>
          {current.code}
        </span>
        <span style={{ fontSize: 12, color: 'var(--dim)' }}>
          {t('currency.example', { amount: formatMoney(1250000) })}
        </span>
      </div>

      <p style={{ fontSize: 11.5, color: 'var(--dim)', margin: 0, lineHeight: 1.65, maxWidth: 620 }}>
        {t('currency.scope_hint')}
      </p>

      {isAdmin ? (
        <>
          <select
            value={current.code}
            disabled={saving}
            onChange={e => void change(e.target.value)}
            aria-label={t('currency.section_title')}
            style={{
              width: '100%', maxWidth: 280, padding: '8px 10px', borderRadius: 8,
              border: '1px solid var(--border-strong)', background: 'var(--surface)',
              color: 'var(--text)', fontSize: 12.5,
            }}
          >
            {supported.map(c => (
              <option key={c.code} value={c.code}>
                {c.code} — {c.symbol}
              </option>
            ))}
          </select>
          <div style={{
            display: 'flex', gap: 7, alignItems: 'flex-start',
            fontSize: 11.5, color: 'var(--warning)', lineHeight: 1.6, maxWidth: 620,
          }}>
            <AlertTriangle size={13} style={{ flexShrink: 0, marginTop: 2 }} />
            {t('currency.relabel_warning')}
          </div>
        </>
      ) : (
        <p style={{ fontSize: 11.5, color: 'var(--dim)', margin: 0, lineHeight: 1.6 }}>
          {t('currency.admin_only')}
        </p>
      )}
    </div>
  )
}
