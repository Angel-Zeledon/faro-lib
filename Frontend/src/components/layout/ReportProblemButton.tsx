'use client'
import { Bug } from 'lucide-react'
import { useLanguage } from '@/contexts/LanguageContext'
import { useBugReport } from '@/lib/bugReport'

/** Always in the top bar: something wrong can be reported from any screen,
 *  not only from the ones that caught their own error (lib/bugReport.ts). */
export default function ReportProblemButton() {
  const { t } = useLanguage()
  const reportBug = useBugReport()
  return (
    <button
      onClick={() => reportBug()}
      title={t('bugreport.topbar')}
      aria-label={t('bugreport.topbar')}
      className="btn"
      style={{
        display: 'inline-flex', alignItems: 'center', justifyContent: 'center',
        width: 32, height: 32, borderRadius: 8, cursor: 'pointer',
        background: 'transparent', border: '1px solid var(--border)', color: 'var(--dim)',
      }}
    >
      <Bug size={15} aria-hidden="true" />
    </button>
  )
}
