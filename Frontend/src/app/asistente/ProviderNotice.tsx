'use client'
/**
 * The sentence above the assistant that says, BEFORE anybody types, why it
 * cannot answer with AI right now — and which of two very different reasons:
 *
 *  - this installation has no StockAI model (the old banner), or
 *  - the company's OWN provider is failing or paused: its name and the exact
 *    problem with its remedy (`errors.llm_<code>`), the same sentence the
 *    settings card shows.
 *
 * An admin gets the way out in the same line: a link to /modelo-ia. Anyone
 * else is told to ask one. Renders nothing while everything works.
 */
import Link from 'next/link'
import { AlertTriangle } from 'lucide-react'
import { useLanguage } from '@/contexts/LanguageContext'
import { useCapabilities } from '@/lib/capabilities'

const LABELS: Record<string, string> = {
  openai: 'OpenAI', anthropic: 'Claude (Anthropic)', google: 'Google Gemini',
  groq: 'Groq', openrouter: 'OpenRouter', deepseek: 'DeepSeek',
}

export default function ProviderNotice({ isAdmin, compact = false }: { isAdmin: boolean; compact?: boolean }) {
  const { t } = useLanguage()
  const { caps, can } = useCapabilities()
  const ap = caps?.assistant_provider
  const companyTrouble = ap?.source === 'company' && (ap.state === 'failing' || ap.state === 'paused')
  const instanceOff = !can('assistant') && !companyTrouble
  if (!companyTrouble && !instanceOff) return null

  const name = ap?.provider ? (LABELS[ap.provider] ?? t('aiProvider.custom_name')) : ''
  return (
    <div role="status" data-testid="assistant-provider-notice" style={{
      display: 'flex', gap: 8, alignItems: 'flex-start',
      // `compact` is the phone layout: full width, the size of its body text.
      padding: compact ? '12px 14px' : '12px 16px', margin: compact ? '0 0 12px' : '12px 16px 0',
      borderRadius: compact ? 12 : 8, background: 'var(--surface-2)', border: '1px solid var(--border)',
      fontSize: compact ? 13.5 : 12, color: 'var(--text)', lineHeight: 1.55, flexShrink: 0,
    }}>
      <AlertTriangle size={13} style={{ flexShrink: 0, marginTop: 2, color: '#B7791F' }} aria-hidden="true" />
      <span style={{ minWidth: 0, overflowWrap: 'anywhere' }}>
        {companyTrouble ? (
          <>
            {t(ap!.state === 'paused' ? 'aiProvider.banner_paused' : 'aiProvider.banner_failing', { provider: name })}{' '}
            {ap!.reason ? t(`errors.llm_${ap!.reason}`) : null}{' '}
            {t('aiProvider.banner_meanwhile')}{' '}
            {isAdmin
              ? <Link href="/modelo-ia" style={{ color: 'var(--accent)', fontWeight: 600 }}>{t('aiProvider.fix_link')}</Link>
              : t('aiProvider.banner_ask_admin')}
          </>
        ) : (
          <>
            {t('analyst.unavailable_banner')}
            {isAdmin && (
              <>
                {' '}{t('aiProvider.choose_own_hint')}{' '}
                <Link href="/modelo-ia" style={{ color: 'var(--accent)', fontWeight: 600 }}>{t('aiProvider.choose_link')}</Link>
              </>
            )}
          </>
        )}
      </span>
    </div>
  )
}
