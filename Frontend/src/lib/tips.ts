import {
  ArrowLeftRight, Bell, Coins, FileDown, FlaskConical, MessageSquare,
  Tags, TrafficCone, Truck, Upload, type LucideIcon,
} from 'lucide-react'

/**
 * Short things worth knowing inside StockAI, shown like a game's loading-screen
 * tips: rotating on the login panel and one at a time while the app opens.
 *
 * Each one names something the product actually does — the copy lives in
 * translations.ts under `tips.<n>.title` / `tips.<n>.body`. A tip about a
 * feature that changes has to change with it, so keep this list short.
 */
export interface Tip { n: number; icon: LucideIcon }

export const TIPS: Tip[] = [
  { n: 1,  icon: TrafficCone },
  { n: 2,  icon: Truck },
  { n: 3,  icon: FlaskConical },
  { n: 4,  icon: FileDown },
  { n: 5,  icon: MessageSquare },
  { n: 6,  icon: Coins },
  { n: 7,  icon: ArrowLeftRight },
  { n: 8,  icon: Upload },
  { n: 9,  icon: Tags },
  { n: 10, icon: Bell },
]

/** A random starting point, so a returning user does not always see tip 1. */
export function randomTipIndex(): number {
  return Math.floor(Math.random() * TIPS.length)
}
