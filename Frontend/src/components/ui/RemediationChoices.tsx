'use client'

/**
 * The pre-training gate's questions, and the user's answers.
 *
 * The product rule, in the owner's words: *the standard we state is met, or
 * there is no forecast and we say why* — and where a fix exists, offer it with
 * its consequence explained so the user chooses.
 *
 * Before this existed the backend gate was already refusing to train these
 * files, and the frontend had nowhere to answer: a file with duplicated rows or
 * an ambiguous date format was blocked with "decide what to do first" and no
 * way to decide. A dead end is worse than the soft warning it replaced.
 *
 * Each option shows BOTH what it does and what it costs. That pairing is the
 * whole point: "fill the gaps with zero" and "interpolate the gaps" are not two
 * flavours of one button, they are two different claims about what happened,
 * and only the user knows which is true.
 */

import { AlertTriangle, Check } from 'lucide-react'
import { useLanguage } from '@/contexts/LanguageContext'
import type { DataQualityIssue, RemediationOption } from '@/lib/types'

/** Spanish from the catalogue when we have it, the engine's English when we
 *  don't — a rough sentence beats a raw key, and beats silence. */
function useGateCopy() {
  const { t } = useLanguage()
  return (key: string, fallback: string, params?: Record<string, unknown>) => {
    const text = t(key, params as Record<string, unknown>)
    return text === key ? fallback : text
  }
}

export default function RemediationChoices({
  issues, chosen, onChoose, disabled = false,
}: {
  issues:   DataQualityIssue[]
  chosen:   Record<string, string>
  onChoose: (issueType: string, optionCode: string) => void
  disabled?: boolean
}) {
  const { t } = useLanguage()
  const copy = useGateCopy()

  const withOptions = (issues || []).filter(i => (i.remediations?.length ?? 0) > 0)
  const fixable = withOptions.filter(i => i.classification === 'blocking_fixable')
  // Advisory findings that still carry options — outliers are the case: real
  // retail is full of extreme values (a wholesale order, a promotion), so
  // blocking on them would stop nearly every genuine file. The CHOICE is still
  // worth offering, because clipping a real peak season means under-ordering
  // next year, and only the user knows which it was. Optional, never gating.
  const optional = withOptions.filter(i => i.classification === 'advisory')

  if (fixable.length === 0 && optional.length === 0) return null

  const answered = fixable.filter(i => chosen[i.type]).length

  return (
    <>
      {fixable.length > 0 && (
        <section
          style={{
            marginTop: 16, border: '1px solid var(--border)',
            borderLeft: '4px solid #f59e0b', borderRadius: 10, padding: '14px 16px',
          }}
        >
          <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
            <AlertTriangle size={16} color="#f59e0b" />
            <span style={{ fontSize: 13.5, fontWeight: 700, color: 'var(--text)' }}>
              {t('gate.decide_title')}
            </span>
            <span style={{ marginLeft: 'auto', fontSize: 11, color: 'var(--dim)' }}>
              {t('gate.answered_count')
                .replace('{answered}', String(answered))
                .replace('{total}', String(fixable.length))}
            </span>
          </div>
          <p style={{ fontSize: 12, color: 'var(--dim)', marginTop: 4, lineHeight: 1.55 }}>
            {t('gate.decide_subtitle')}
          </p>

          <div style={{ display: 'flex', flexDirection: 'column', gap: 16, marginTop: 14 }}>
            {fixable.map(issue => (
              <IssueQuestion
                key={issue.type}
                issue={issue}
                selected={chosen[issue.type]}
                onChoose={code => onChoose(issue.type, code)}
                disabled={disabled}
                copy={copy}
              />
            ))}
          </div>
        </section>
      )}

      {optional.length > 0 && (
        <section
          style={{
            marginTop: 12, border: '1px solid var(--border)',
            borderRadius: 10, padding: '14px 16px',
          }}
        >
          <span style={{ fontSize: 13.5, fontWeight: 700, color: 'var(--text)' }}>
            {t('gate.optional_title')}
          </span>
          <p style={{ fontSize: 12, color: 'var(--dim)', marginTop: 4, lineHeight: 1.55 }}>
            {t('gate.optional_subtitle')}
          </p>
          <div style={{ display: 'flex', flexDirection: 'column', gap: 16, marginTop: 14 }}>
            {optional.map(issue => (
              <IssueQuestion
                key={issue.type}
                issue={issue}
                selected={chosen[issue.type]}
                onChoose={code => onChoose(issue.type, code)}
                disabled={disabled}
                copy={copy}
              />
            ))}
          </div>
        </section>
      )}
    </>
  )
}

function IssueQuestion({ issue, selected, onChoose, disabled, copy }: {
  issue:    DataQualityIssue
  selected?: string
  onChoose: (code: string) => void
  disabled: boolean
  copy:     (key: string, fallback: string, params?: Record<string, unknown>) => string
}) {
  const { t } = useLanguage()
  const params = { ...(issue.params ?? {}), ...issue }
  const title = copy(`gate.issue.${issue.type}`, issue.message, params)

  return (
    <fieldset style={{ border: 'none', margin: 0, padding: 0 }}>
      <legend style={{ fontSize: 12.5, fontWeight: 600, color: 'var(--text)', padding: 0 }}>
        {title}
      </legend>
      <div
        role="radiogroup"
        aria-label={title}
        style={{ display: 'flex', flexDirection: 'column', gap: 6, marginTop: 8 }}
      >
        {(issue.remediations ?? []).map((opt: RemediationOption) => {
          const isSelected = selected === opt.code
          const optParams = { ...(opt.params ?? {}) }
          return (
            <button
              key={opt.code}
              type="button"
              role="radio"
              aria-checked={isSelected}
              disabled={disabled}
              onClick={() => onChoose(opt.code)}
              style={{
                all: 'unset',
                cursor: disabled ? 'not-allowed' : 'pointer',
                display: 'flex', gap: 10, alignItems: 'flex-start',
                padding: '10px 12px', borderRadius: 8,
                border: `1px solid ${isSelected ? 'var(--accent)' : 'var(--border)'}`,
                background: isSelected
                  ? 'color-mix(in srgb, var(--accent) 7%, transparent)'
                  : 'transparent',
                opacity: disabled ? 0.6 : 1,
              }}
            >
              <span
                aria-hidden
                style={{
                  width: 16, height: 16, borderRadius: 8, flexShrink: 0, marginTop: 1,
                  border: `1.5px solid ${isSelected ? 'var(--accent)' : 'var(--border)'}`,
                  display: 'flex', alignItems: 'center', justifyContent: 'center',
                  color: 'var(--accent)',
                }}
              >
                {isSelected && <Check size={11} strokeWidth={3} />}
              </span>
              <span style={{ display: 'block' }}>
                <span style={{ fontSize: 12.5, color: 'var(--text)', lineHeight: 1.5 }}>
                  {copy(`gateopt.${opt.code}.action`, opt.action, optParams)}
                  {opt.recommended && (
                    <span style={{
                      marginLeft: 6, fontSize: 9, fontWeight: 700, letterSpacing: '0.04em',
                      textTransform: 'uppercase', color: 'var(--accent)',
                    }}>
                      {t('gate.recommended')}
                    </span>
                  )}
                </span>
                {/* Never hidden behind a tooltip: the consequence is the reason
                    this is a choice and not a default. */}
                <span style={{
                  display: 'block', fontSize: 11.5, color: 'var(--dim)',
                  marginTop: 3, lineHeight: 1.5,
                }}>
                  {copy(`gateopt.${opt.code}.consequence`, opt.consequence, optParams)}
                </span>
              </span>
            </button>
          )
        })}
      </div>
    </fieldset>
  )
}
