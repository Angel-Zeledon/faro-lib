'use client'
import Link from 'next/link'
import { usePathname } from 'next/navigation'
import { CheckCircle2, AlertTriangle } from 'lucide-react'
import { useTraining } from '@/contexts/TrainingContext'
import { useLanguage } from '@/contexts/LanguageContext'
import { useSmoothedPercent } from '@/hooks/useSmoothedPercent'

// The screen that owns a training already shows its own progress.
const TRAINING_SCREEN_PATHS = ['/quick-start', '/ventas']

/**
 * Persistent, quiet "training in progress" pill for the top bar.
 *
 * Shown while the server reports a run queued/running, links to the training
 * screen (which resumes from server state), and says "finished"/"failed" once
 * when the run ends. Renders nothing otherwise.
 */
export default function TrainingPill() {
  const training = useTraining()
  const path = usePathname()
  const { t } = useLanguage()
  const family = training?.families[0] ?? null
  const shown = useSmoothedPercent(family ? family.percent : null)

  if (!training) return null
  if (TRAINING_SCREEN_PATHS.includes(path ?? '')) return null

  const base = {
    display: 'inline-flex', alignItems: 'center', gap: 8,
    padding: '4px 10px', borderRadius: 999,
    border: '1px solid var(--border)', background: 'var(--surface)',
    fontSize: 12, fontWeight: 600, color: 'var(--text)',
    textDecoration: 'none', whiteSpace: 'nowrap' as const,
  }

  if (family) {
    const stepKey = family.step ? `qs.stage_${family.step}` : ''
    const stageText = stepKey && t(stepKey as never) !== stepKey ? t(stepKey as never) : (family.message ?? '')
    return (
      <Link href="/quick-start" style={base} title={stageText} aria-live="polite"
            data-testid="training-pill">
        <span aria-hidden style={{
          width: 10, height: 10, borderRadius: '50%',
          border: '2px solid var(--border)', borderTopColor: 'var(--accent)',
          animation: 'tp-spin 0.9s linear infinite', flexShrink: 0,
        }} />
        <style>{'@keyframes tp-spin { to { transform: rotate(360deg) } }'}</style>
        <span>{t('training_pill.running')}</span>
        {shown != null && <span style={{ fontVariantNumeric: 'tabular-nums', color: 'var(--dim)' }}>{shown}%</span>}
      </Link>
    )
  }

  const outcome = training.outcome
  if (outcome) {
    const failed = outcome.status !== 'COMPLETED'
    return (
      <Link href={failed ? '/quick-start' : '/pronosticos'} style={base} aria-live="polite"
            data-testid="training-pill-outcome">
        {failed
          ? <AlertTriangle size={13} color="var(--danger, #dc2626)" />
          : <CheckCircle2 size={13} color="var(--accent)" />}
        <span>{t(failed ? 'training_pill.failed' : 'training_pill.done')}</span>
      </Link>
    )
  }
  return null
}
