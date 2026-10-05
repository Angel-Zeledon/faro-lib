'use client'
import { useEffect, useState } from 'react'
import { Info } from 'lucide-react'
import { useLanguage } from '@/contexts/LanguageContext'
import { Wordmark } from '@/components/brand/Wordmark'
import { TIPS, randomTipIndex } from '@/lib/tips'

// The right half of /login. Signup sells the product (AuthPanel); somebody
// signing in already bought it, so this side gives them something to use
// instead: one tip at a time, rotating like a loading screen's, with a bar
// that fills until the next one. Hovering pauses it (and the bar starts over
// with the timer when it resumes); the dots jump. The tip is real text, read
// politely by screen readers; the rotation never moves focus.

const STEP_MS = 6500

export function TipsPanel() {
  const { t } = useLanguage()
  // Fixed first render (server and client agree), randomised after mount.
  const [i, setI] = useState(0)
  const [paused, setPaused] = useState(false)

  useEffect(() => { setI(randomTipIndex()) }, [])

  useEffect(() => {
    if (paused) return
    const id = setTimeout(() => setI(n => (n + 1) % TIPS.length), STEP_MS)
    return () => clearTimeout(id)
  }, [i, paused])

  const tip = TIPS[i]
  const Icon = tip.icon

  return (
    <aside
      className="auth-panel tips-panel"
      onMouseEnter={() => setPaused(true)}
      onMouseLeave={() => setPaused(false)}
    >
      <div className="auth-panel-mark" aria-hidden="true">
        <Wordmark size={190} color="rgba(255,255,255,0.045)" accent="rgba(43,167,154,0.16)" />
      </div>
      <div className="auth-panel-inner">
        <p className="tips-kicker">
          <Info size={14} aria-hidden="true" />
          {t('tips.kicker')}
        </p>

        {/* Keyed on the tip: each change remounts it and replays the entrance. */}
        <div key={tip.n} className="tips-card" aria-live="polite">
          <span className="tips-icon" aria-hidden="true"><Icon size={26} strokeWidth={1.8} /></span>
          <h2 className="tips-title">{t(`tips.${tip.n}.title`)}</h2>
          <p className="tips-body">{t(`tips.${tip.n}.body`)}</p>
        </div>

        <div className="tips-progress" aria-hidden="true">
          <span
            key={`${tip.n}-${paused}`}
            className="tips-progress-fill"
            style={{ animationDuration: `${STEP_MS}ms`, animationPlayState: paused ? 'paused' : 'running' }}
          />
        </div>

        <div className="tips-dots">
          {TIPS.map((x, k) => (
            <button
              key={x.n}
              type="button"
              className={`tips-dot${k === i ? ' is-on' : ''}`}
              onClick={() => setI(k)}
              aria-label={t('tips.goto', { n: k + 1 })}
              aria-current={k === i}
            />
          ))}
          <span className="tips-count">{t('tips.counter', { n: i + 1, total: TIPS.length })}</span>
        </div>
      </div>
    </aside>
  )
}
