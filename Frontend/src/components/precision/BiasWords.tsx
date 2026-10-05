'use client'
import { useLanguage } from '@/contexts/LanguageContext'
import { fmtNum } from '@/lib/numberLocale'

/** Below this magnitude (fraction of demand) the bias reads as "balanced". */
const BALANCED_BELOW = 0.005

/**
 * Forecast bias stated in words: over-forecast, under-forecast or balanced, with
 * the size as a percentage of demand. The sign alone ("-12%") leaves the buyer to
 * work out which way it errs. Accent colour on purpose: the semaforo colours
 * mean stock status and must not be reused for forecast direction.
 */
export default function BiasWords({ bias }: { bias: number | null | undefined }) {
  const { t } = useLanguage()
  if (bias == null || !Number.isFinite(bias)) return <span>—</span>
  if (Math.abs(bias) < BALANCED_BELOW) {
    return <span style={{ color: 'var(--muted)' }}>{t('precision.bias_balanced')}</span>
  }
  const pct = fmtNum(Math.abs(bias) * 100, { maximumFractionDigits: 1 })
  return (
    <span style={{ color: 'var(--accent)' }}>
      {t(bias > 0 ? 'precision.bias_over' : 'precision.bias_under', { pct })}
    </span>
  )
}
