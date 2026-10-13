'use client'
/**
 * /modelo-ia — which AI model this company's assistant, summaries and data
 * diagnosis run on: StockAI's default, or the company's own provider account.
 * Reached from Configuración and from the assistant when it is unavailable or
 * its provider is failing. The panel itself is `components/ai/AiProviderPanel`.
 */
import AiProviderPanel from '@/components/ai/AiProviderPanel'

export default function AiModelPage() {
  return (
    <div style={{ width: '100%', maxWidth: 720, margin: '0 auto' }}>
      <AiProviderPanel />
    </div>
  )
}
