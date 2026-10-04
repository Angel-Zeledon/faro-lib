'use client'
import { useLanguage } from '@/contexts/LanguageContext'
import { Wordmark } from '@/components/brand/Wordmark'

// The right half of /login and /signup: what the app hands a buyer every
// morning, drawn in the product's own vocabulary — one line per product, the
// semáforo state, the quantity. Decorative (aria-hidden): the rows are an
// illustration of the list, not anybody's data, and they carry no figures the
// product claims about itself.
//
// Colours are the dark-surface semáforo tones from globals.css, fixed here
// because the panel is always petrol regardless of the app theme.

type Signal = 'now' | 'soon' | 'ok' | 'over'

const SIGNAL_COLOR: Record<Signal, string> = {
  now:  '#D07878',
  soon: '#C99A3E',
  ok:   '#4ade80',
  over: '#93c5fd',
}

const SIGNAL_LABEL_KEY: Record<Signal, string> = {
  now:  'inventory.signal_order_now',
  soon: 'inventory.signal_order_soon',
  ok:   'inventory.signal_ok',
  over: 'inventory.signal_overstock',
}

const ROWS: { n: 1 | 2 | 3 | 4; signal: Signal; qty: number | null }[] = [
  { n: 1, signal: 'now',  qty: 120 },
  { n: 2, signal: 'now',  qty: 48 },
  { n: 3, signal: 'soon', qty: 36 },
  { n: 4, signal: 'over', qty: null },
]

export function AuthPanel() {
  const { t } = useLanguage()

  return (
    <aside className="auth-panel" aria-hidden="true">
      {/* The brand, oversized and bled off the corner: the one bold gesture
          on this screen. */}
      <div className="auth-panel-mark">
        <Wordmark size={190} color="rgba(255,255,255,0.045)" accent="rgba(43,167,154,0.16)" />
      </div>
      <div className="auth-panel-inner">
        <h2 className="auth-panel-title">{t('auth.panel_title')}</h2>

        <div className="auth-panel-list">
          <div className="auth-panel-list-head">
            <span className="auth-panel-live">
              <span className="auth-panel-pulse" />
              {t('auth.panel_list_title')}
            </span>
            <span>{t('auth.panel_list_qty')}</span>
          </div>
          {ROWS.map((row, i) => (
            <div
              key={row.n}
              className="auth-panel-row"
              style={{ animationDelay: `${0.25 + i * 0.12}s` }}
            >
              <span className="auth-panel-dot" style={{ background: SIGNAL_COLOR[row.signal] }} />
              <span className="auth-panel-name">
                <span>{t(`auth.panel_row_${row.n}_name`)}</span>
                <span className="auth-panel-supplier">{t(`auth.panel_row_${row.n}_supplier`)}</span>
              </span>
              <span className="auth-panel-signal" style={{ color: SIGNAL_COLOR[row.signal] }}>
                {t(SIGNAL_LABEL_KEY[row.signal])}
              </span>
              <span className="auth-panel-qty">
                {row.qty != null ? row.qty.toLocaleString() : t('auth.panel_no_order')}
              </span>
            </div>
          ))}
        </div>

        <p className="auth-panel-note">{t('auth.panel_note')}</p>
      </div>
    </aside>
  )
}
