'use client'
import { useState, useEffect } from 'react'
import { FileText, ChevronDown, ChevronUp, AlertTriangle, CheckCircle2, Clock, ExternalLink } from 'lucide-react'
import Link from 'next/link'
import { useIsNarrow } from '@/hooks/useIsNarrow'
import { useLanguage } from '@/contexts/LanguageContext'
import { Markdown } from '@/components/ui/Markdown'

type Urgency = 'critical' | 'warning' | 'ok'

// `labelKey` rather than a label: the urgency chip is copy, and copy lives in
// the i18n catalog (CLAUDE.md). `fallback` is the English of last resort, so an
// unmapped key never renders as "narrative.urgency_ok" at the user.
const URGENCY_CFG: Record<Urgency, {
  border: string; bg: string; icon: React.ElementType; iconColor: string
  labelKey: string; labelFallback: string
}> = {
  critical: { border: 'rgba(192,80,77,0.3)',  bg: 'rgba(192,80,77,0.04)',  icon: AlertTriangle, iconColor: '#C0504D', labelKey: 'narrative.urgency_critical', labelFallback: 'Needs attention' },
  warning:  { border: 'rgba(183,121,31,0.3)', bg: 'rgba(183,121,31,0.04)', icon: Clock,         iconColor: '#B7791F', labelKey: 'narrative.urgency_warning',  labelFallback: 'Review this week' },
  ok:       { border: 'rgba(46,139,98,0.3)',  bg: 'rgba(46,139,98,0.04)',  icon: CheckCircle2,  iconColor: '#2E8B62', labelKey: 'narrative.urgency_ok',       labelFallback: 'Under control' },
}

// Narratives mark a section title as a line that is entirely **bold**; the
// shared renderer shows those as its quiet small-caps h4.
function RenderNarrative({ text }: { text: string }) {
  const source = text.replace(/^[ \t]*\*\*([^*\n]+)\*\*[ \t]*$/gm, '#### $1')
  return <Markdown text={source} />
}

interface NarrativeCardProps {
  title?:         string
  narrative:      string | null
  keyPoints?:     string[]
  urgency?:       Urgency
  loading?:       boolean
  fallback?:      boolean
  analytistLink?: string  // link to open analyst with context
  compact?:       boolean // shorter display
  onRefresh?:     () => void
}

export default function NarrativeCard({
  title,
  narrative, keyPoints = [], urgency = 'ok',
  loading = false, fallback = false,
  analytistLink, compact = false, onRefresh,
}: NarrativeCardProps) {
  const { t } = useLanguage()
  // Its two footer buttons are 20px tall on desktop; 44px on a phone.
  const narrow = useIsNarrow()
  const tap: React.CSSProperties = narrow
    ? { minHeight: 44, boxSizing: 'border-box', display: 'inline-flex', alignItems: 'center', padding: '0 12px', fontSize: 13, borderRadius: 10 }
    : {}
  const [expanded, setExpanded] = useState(!compact)
  const [visible,  setVisible]  = useState(false)
  const cfg  = URGENCY_CFG[urgency]
  const Icon = cfg.icon

  /** `t` echoes the key back when the catalog has no entry — show the English
   *  sentence instead of the key itself. */
  const copy = (key: string, fallbackText: string) => {
    const rendered = t(key)
    return rendered === key ? fallbackText : rendered
  }

  const cardTitle = title ?? copy('narrative.title', 'StockAI analysis')

  useEffect(() => {
    if (narrative && !loading) {
      const timer = setTimeout(() => setVisible(true), 80)
      return () => clearTimeout(timer)
    }
  }, [narrative, loading])

  return (
    <div style={{
      background: cfg.bg,
      border: `1px solid ${cfg.border}`,
      borderRadius: 12, overflow: 'hidden',
      transition: 'all 0.2s',
    }}>
      {/* Header */}
      <div
        onClick={() => !loading && setExpanded(v => !v)}
        style={{
          display: 'flex', alignItems: 'center', gap: 10,
          padding: compact ? '10px 14px' : '14px 18px',
          cursor: loading ? 'default' : 'pointer',
          userSelect: 'none' as const,
        }}
      >
        <div style={{
          width: 28, height: 28, borderRadius: 7, flexShrink: 0,
          background: 'color-mix(in srgb, var(--accent) 12%, transparent)', border: '1px solid color-mix(in srgb, var(--accent) 20%, transparent)',
          display: 'flex', alignItems: 'center', justifyContent: 'center',
        }}>
          <FileText size={13} color="var(--accent)" />
        </div>

        <div style={{ flex: 1, minWidth: 0 }}>
          <div style={{ fontSize: 12, fontWeight: 700, color: 'var(--text)' }}>{cardTitle}</div>
          {!loading && !expanded && keyPoints.length > 0 && (
            <div style={{ fontSize: 11, color: cfg.iconColor, marginTop: 1, overflow: 'hidden', overflowWrap: 'anywhere', }}>
              {keyPoints[0]}
            </div>
          )}
          {loading && (
            <div style={{ fontSize: 11, color: 'var(--dim)', marginTop: 1, display: 'flex', alignItems: 'center', gap: 5 }}>
              <span style={{ width: 8, height: 8, borderRadius: '50%', border: '1.5px solid var(--dim)', borderTopColor: 'var(--accent)', display: 'inline-block', animation: 'spin 0.7s linear infinite' }} />
              {copy('narrative.analyzing', 'Analysing data…')}
            </div>
          )}
        </div>

        <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
          {!loading && (
            <span style={{
              fontSize: 10, fontWeight: 700, padding: '2px 8px', borderRadius: 20,
              background: cfg.iconColor + '18', color: cfg.iconColor,
              display: 'flex', alignItems: 'center', gap: 4,
            }}>
              <Icon size={9} /> {copy(cfg.labelKey, cfg.labelFallback)}
            </span>
          )}
          {fallback && !loading && (
            <span style={{ fontSize: 9, color: 'var(--dim)', padding: '1px 6px', borderRadius: 10, border: '1px solid var(--border)' }}>{copy('narrative.rules_badge', 'rules')}</span>
          )}
          {!loading && (expanded ? <ChevronUp size={14} color="var(--dim)" /> : <ChevronDown size={14} color="var(--dim)" />)}
        </div>
      </div>

      {/* Body */}
      {expanded && !loading && narrative && (
        <div style={{
          padding: compact ? '0 14px 12px' : '0 18px 16px',
          borderTop: `1px solid ${cfg.border}`,
          paddingTop: 12,
          opacity: visible ? 1 : 0,
          transform: visible ? 'translateY(0)' : 'translateY(6px)',
          transition: 'opacity 0.4s ease, transform 0.4s ease',
        }}>
          <RenderNarrative text={narrative} />

          {/* Footer */}
          <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginTop: 14, paddingTop: 10, borderTop: `1px solid ${cfg.border}`, ...(narrow ? { flexWrap: 'wrap', gap: 10 } : {}) }}>
            <div style={{ fontSize: 10, color: 'var(--dim)', display: 'flex', alignItems: 'center', gap: 4 }}>
              <FileText size={9} color="var(--dim)" />
              {fallback
                ? copy('narrative.footer_rules', 'Rule-based analysis')
                : copy('narrative.footer_ai', 'Generated by StockAI · Grounded in your data')}
            </div>
            <div style={{ display: 'flex', gap: 8 }}>
              {onRefresh && (
                <button onClick={e => { e.stopPropagation(); onRefresh() }} style={{ all: 'unset', cursor: 'pointer', fontSize: 11, color: 'var(--dim)', padding: '2px 8px', borderRadius: 5, border: '1px solid var(--border)', ...tap }}>
                  {copy('narrative.refresh', 'Refresh')}
                </button>
              )}
              {analytistLink && (
                <Link href={analytistLink} style={{
                  display: 'flex', alignItems: 'center', gap: 4,
                  fontSize: 11, color: 'var(--accent)', textDecoration: 'none',
                  padding: '2px 8px', borderRadius: 5,
                  border: '1px solid color-mix(in srgb, var(--accent) 30%, transparent)',
                  background: 'color-mix(in srgb, var(--accent) 6%, transparent)',
                  ...tap,
                }}>
                  <ExternalLink size={9} aria-hidden="true" /> {t('narrative.ask_analyst')}
                </Link>
              )}
            </div>
          </div>
        </div>
      )}

      {/* Loading skeleton */}
      {loading && (
        <div style={{ padding: '0 18px 16px', borderTop: `1px solid ${cfg.border}`, paddingTop: 12 }}>
          {[100, 80, 95, 70].map((w, i) => (
            <div key={i} style={{
              height: 11, borderRadius: 4, marginBottom: 8,
              width: `${w}%`, background: 'var(--surface-2)',
              animation: `narrative-pulse 1.5s ease-in-out ${i * 0.1}s infinite`,
            }} />
          ))}
          {/* Both keyframes live in globals.css. This card used to inject a
              `pulse` of its own, and so did the analyst screen — with a
              different shape under the same name, so one silently overrode
              the other document-wide. */}
        </div>
      )}
    </div>
  )
}
