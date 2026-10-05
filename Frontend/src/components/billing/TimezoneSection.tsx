'use client'
/**
 * The clock the company's scheduled retrains run on.
 *
 * The frequency picker in Automatización names an hour ("cada lunes a las 6am"),
 * and the cron behind it used to be read in UTC — so a Costa Rican admin chose
 * 6am and the screen answered "Próxima ejecución: 12:00 a.m.", six hours off,
 * with no timezone named anywhere. This is where that hour gets an owner.
 *
 * Deliberately built as a twin of CurrencySection: same shell, same admin-only
 * write, same "here is what it means" line, so a tenant preference always looks
 * and behaves the same way.
 */
import { useCallback, useEffect, useState } from 'react'
import { Clock } from 'lucide-react'

import Spinner from '@/components/ui/Spinner'
import { getTenantTimezone, setTenantTimezone, type TenantTimezone } from '@/lib/api'
import { getUser } from '@/lib/auth'
import { useLanguage } from '@/contexts/LanguageContext'
import { timezoneLabel } from '@/lib/enumLabels'
import { useToast } from '@/contexts/ToastContext'

export default function TimezoneSection() {
  const { t, lang } = useLanguage()
  const zoneLabel = (z: TenantTimezone) => timezoneLabel(t, z.timezone, z.label)
  const toast = useToast()
  const isAdmin = getUser()?.role === 'admin'

  const [current, setCurrent] = useState<TenantTimezone | null>(null)
  const [supported, setSupported] = useState<TenantTimezone[]>([])
  const [loading, setLoading] = useState(true)
  const [saving, setSaving] = useState(false)

  const load = useCallback(() => {
    getTenantTimezone()
      .then(d => { setCurrent(d.current); setSupported(d.supported || []) })
      .catch(() => setCurrent(null))
      .finally(() => setLoading(false))
  }, [])

  useEffect(load, [load])

  const change = async (timezone: string) => {
    if (!timezone || timezone === current?.timezone) return
    setSaving(true)
    try {
      const { current: next } = await setTenantTimezone(timezone)
      setCurrent(next)
      // Changing the zone moves when every armed retrain fires, and those next-run
      // times are already rendered on the automation screen. Say so rather than
      // leaving a stale hour on another tab.
      toast.addToast(t('timezone.section_title'), t('timezone.changed'), 'success')
    } catch {
      /* interceptor surfaced it */
    } finally {
      setSaving(false)
    }
  }

  if (loading) return <div style={{ padding: 8 }}><Spinner size={14} /></div>
  if (!current) return null

  // What the company's clock says right now — the fastest way for someone to
  // confirm the zone is the one they meant.
  const nowThere = new Date().toLocaleString(lang, {
    timeZone: current.timezone, dateStyle: 'short', timeStyle: 'short',
  })

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
      <div style={{
        display: 'flex', alignItems: 'center', gap: 9,
        padding: '10px 12px', borderRadius: 9,
        background: 'var(--surface-2)', border: '1px solid var(--border)',
      }}>
        <Clock size={15} color="var(--muted)" aria-hidden="true" />
        <span style={{ fontSize: 13, fontWeight: 600, color: 'var(--text)' }}>
          {zoneLabel(current)}
        </span>
        <span style={{ fontSize: 12, color: 'var(--dim)' }}>
          {t('timezone.now_there', { time: nowThere })}
        </span>
      </div>

      <p style={{ fontSize: 11.5, color: 'var(--dim)', margin: 0, lineHeight: 1.65, maxWidth: 620 }}>
        {t('timezone.scope_hint')}
      </p>

      {isAdmin ? (
        <select
          value={current.timezone}
          disabled={saving}
          onChange={e => void change(e.target.value)}
          aria-label={t('timezone.section_title')}
          style={{
            width: '100%', maxWidth: 280, padding: '8px 10px', borderRadius: 8,
            border: '1px solid var(--border-strong)', background: 'var(--surface)',
            color: 'var(--text)', fontSize: 12.5,
          }}
        >
          {supported.map(z => (
            <option key={z.timezone} value={z.timezone}>{zoneLabel(z)}</option>
          ))}
        </select>
      ) : (
        <p style={{ fontSize: 11.5, color: 'var(--dim)', margin: 0, lineHeight: 1.6 }}>
          {t('timezone.admin_only')}
        </p>
      )}
    </div>
  )
}
