'use client'
/**
 * Suggested service level per ABC class.
 *
 * The A/B/C letter is how much money a product moves (cumulative demand value:
 * A up to 80%, B up to 95%, C the rest). Protecting an A product harder than a
 * C one is the standard way to spend a limited safety-stock budget, so each
 * class gets a suggested service level.
 *
 * It is only a suggestion. Nothing changes until a person presses "Apply" on a
 * class, and even then products whose level someone already set — the buyer, a
 * file import, a supplier or category rule — are left alone. The panel says how
 * many of each before the click.
 */
import { useCallback, useEffect, useState } from 'react'

import Button from '@/components/ui/Button'
import Spinner from '@/components/ui/Spinner'
import { useErrorDetail } from '@/components/ui/States'
import { useConfirm } from '@/components/ui/ConfirmDialog'
import { useToast } from '@/contexts/ToastContext'
import { useLanguage } from '@/contexts/LanguageContext'
import { applyServiceLevelClass, getServiceLevelClasses } from '@/lib/api'
import { getUser } from '@/lib/auth'
import { localeFor } from '@/lib/numberLocale'
import type { AbcClass, ServiceLevelClassRow, ServiceLevelClassesState } from '@/lib/types'

export default function ServiceLevelClassesPanel() {
  const { t, lang } = useLanguage()
  const errorDetail = useErrorDetail()
  const confirm = useConfirm()
  const { addToast } = useToast()
  const role = getUser()?.role
  const canEdit = role === 'admin' || role === 'analyst'

  const [state, setState] = useState<ServiceLevelClassesState | null>(null)
  const [loadFailed, setLoadFailed] = useState(false)
  const [busy, setBusy] = useState<AbcClass | null>(null)
  const [error, setError] = useState<string | null>(null)

  const load = useCallback(async () => {
    try {
      setState(await getServiceLevelClasses({ silent: true }))
      setLoadFailed(false)
    } catch {
      setLoadFailed(true)
    }
  }, [])
  useEffect(() => { void load() }, [load])

  const pct = (v: number | null) =>
    v == null ? '—' : `${(v * 100).toLocaleString(localeFor(lang), { maximumFractionDigits: 1 })}%`

  async function apply(row: ServiceLevelClassRow) {
    if (!canEdit || row.would_change === 0) return
    const ok = await confirm({
      title: t('inventory.slc_confirm_title', { abc: row.abc, level: pct(row.suggested_service_level) }),
      message: t('inventory.slc_confirm_body', { n: row.would_change, owned: row.owned }),
    })
    if (!ok) return
    setBusy(row.abc)
    setError(null)
    try {
      const res = await applyServiceLevelClass(row.abc)
      addToast(
        t('inventory.slc_applied', { abc: res.abc, n: res.updated, level: pct(res.service_level) }),
        res.kept_own_level > 0 ? t('inventory.slc_applied_kept', { n: res.kept_own_level }) : '',
        'success')
      await load()
    } catch (e) {
      setError(errorDetail(e))
    } finally {
      setBusy(null)
    }
  }

  const shell = (children: React.ReactNode) => (
    <section style={sectionStyle} id="nivel-servicio-clases">{children}</section>
  )

  if (!state) {
    return shell(loadFailed
      ? <div style={{ color: 'var(--dim)', fontSize: 13 }}>{t('inventory.slc_load_failed')}</div>
      : <div style={{ textAlign: 'center' }}><Spinner size={18} /></div>)
  }
  if (!state.available) {
    return shell(<>
      <h2 style={titleStyle}>{t('inventory.slc_title')}</h2>
      <p style={{ fontSize: 13, color: 'var(--dim)', margin: '6px 0 0' }}>{t('inventory.slc_unavailable')}</p>
    </>)
  }

  return shell(<>
    <h2 style={titleStyle}>{t('inventory.slc_title')}</h2>
    <p style={{ fontSize: 13, color: 'var(--muted)', margin: '6px 0 14px', lineHeight: 1.6, maxWidth: 760 }}>
      {t('inventory.slc_intro', {
        a: Math.round(state.cutoffs.a * 100), b: Math.round(state.cutoffs.b * 100),
      })}
    </p>
    <div style={{ overflowX: 'auto' }}>
      <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 13, minWidth: 560 }}>
        <thead>
          <tr style={{ textAlign: 'left', color: 'var(--dim)', fontSize: 12 }}>
            <th style={th}>{t('inventory.slc_col_class')}</th>
            <th style={th}>{t('inventory.slc_col_skus')}</th>
            <th style={th}>{t('inventory.slc_col_value')}</th>
            <th style={th}>{t('inventory.slc_col_current')}</th>
            <th style={th}>{t('inventory.slc_col_suggested')}</th>
            <th style={th}>{t('inventory.slc_col_changes')}</th>
            <th style={th} />
          </tr>
        </thead>
        <tbody>
          {state.classes.map(row => (
            <tr key={row.abc} data-testid={`slc-row-${row.abc}`}>
              <td style={td}><strong>{row.abc}</strong></td>
              <td style={td}>{row.skus.toLocaleString(localeFor(lang))}</td>
              <td style={td}>{pct(row.value_share)}</td>
              <td style={td}>{pct(row.current_service_level)}</td>
              <td style={td}>{pct(row.suggested_service_level)}</td>
              <td style={td}>
                {row.would_change.toLocaleString(localeFor(lang))}
                {row.owned > 0 && (
                  <span style={{ color: 'var(--dim)', fontSize: 11.5 }}>
                    {' · '}{t('inventory.slc_owned', { n: row.owned })}
                  </span>
                )}
              </td>
              <td style={{ ...td, textAlign: 'right' }}>
                {canEdit && (
                  <Button
                    size="sm" variant="secondary"
                    disabled={row.would_change === 0 || busy !== null}
                    loading={busy === row.abc}
                    onClick={() => void apply(row)}
                  >
                    {row.would_change === 0 ? t('inventory.slc_nothing') : t('inventory.slc_apply')}
                  </Button>
                )}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
    <p style={{ fontSize: 12, color: 'var(--dim)', margin: '10px 0 0', maxWidth: 760 }}>
      {t('inventory.slc_footnote')}
    </p>
    {error && <p role="alert" style={{ fontSize: 13, color: 'var(--red, #c0392b)', margin: '8px 0 0' }}>{error}</p>}
  </>)
}

const sectionStyle: React.CSSProperties = {
  border: '1px solid var(--border)', borderRadius: 12,
  background: 'var(--surface)', padding: '18px 20px',
}
const titleStyle: React.CSSProperties = { fontSize: 15, fontWeight: 700, color: 'var(--text)', margin: 0 }
const th: React.CSSProperties = { padding: '6px 10px', borderBottom: '1px solid var(--border)', fontWeight: 600 }
const td: React.CSSProperties = { padding: '8px 10px', borderBottom: '1px solid var(--border)', color: 'var(--text)' }
