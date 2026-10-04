/**
 * Sanctioned icon per concept. An icon says what a control DOES, never that
 * "AI is involved". Banned (enforced by scripts/check-icons.mjs): Sparkles,
 * Wand/Wand2, Bot, Brain/BrainCircuit, Stars, Cpu used as an AI cue, plus the
 * sparkle/robot/brain emoji. Lightbulb and Zap are discouraged as "smart" cues.
 *
 * Convention: lucide-react only, default stroke width (use 1.5-1.8 where a
 * size is set explicitly, never mix weights inside one screen), one size per
 * context (14-16 inline, 18-22 tiles, 28 empty states).
 *
 * Use these exports for new code; existing screens import lucide directly
 * using the same choices.
 */
export {
  TrendingUp as ForecastIcon, // forecasting, demand spikes, anticipation
  ShoppingCart as PurchaseIcon, // purchase suggestion / order
  MessageSquare as AssistantIcon, // analyst chat, suggested questions
  FileText as NarrativeIcon, // narrative summary, documents
  ShieldCheck as DataQualityIcon, // data quality checks
  Clock as AutomationIcon, // schedules, recurring jobs
  Repeat as RecurringIcon, // repeat / sync
  Info as ExplanationIcon, // explanations, tips, "how it is calculated"
  Sliders as SimulationIcon, // what-if simulation, tuning
  Plug as IntegrationIcon, // API, MCP, connectors
} from 'lucide-react'
