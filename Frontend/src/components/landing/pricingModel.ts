// PRICES — confirmed by the owner on 2026-10-01 (the agent's proposal, with
// 50,000 API calls a month included so the full plan never includes fewer
// than the free plan's 500 a day).
//
// Every number the pricing calculator shows lives in this file, and nowhere
// else. The landing renders an ESTIMATE from it: there is still no checkout,
// no card and no Stripe (CLAUDE.md). A tenant becomes `paid` because somebody
// talked to us and `tenants.tier` was set; the final price is agreed in that
// conversation, and this model only tells a visitor what to expect before it.
//
// The free plan's ceilings below MUST match backend/entitlements/plans.py
// (FREE). They are repeated here, not imported, because the landing cannot
// reach the backend's Python — if plans.py moves, move these with it.

export const CURRENCY = 'USD'

// ── Free: permanent, every feature, short ceilings (plans.py FREE) ───────────
export const FREE_PLAN = {
  skus: 100,
  users: 2,
  warehouses: 1,
  apiCallsPerDay: 500,
} as const

// ── Full: a monthly base plus what goes past what the base includes ──────────
export const FULL_PLAN = {
  baseMonthly: 39,
  included: {
    skus: 1_000,
    users: 5,
    warehouses: 3,
    apiCallsPerMonth: 50_000,
  },
  // Each add-on is priced per block: `price` for every started `per` units
  // above what the base includes.
  addOns: {
    skus: { per: 500, price: 10 },
    users: { per: 1, price: 5 },
    warehouses: { per: 1, price: 8 },
    apiCalls: { per: 1_000, price: 2 },
  },
} as const

// The ranges the calculator's sliders cover. A number typed into the box can
// go past the slider's end; the estimate keeps counting.
export const CALC_RANGES = {
  skus: { min: 0, max: 20_000, step: 100, initial: 2_500 },
  users: { min: 1, max: 50, step: 1, initial: 6 },
  warehouses: { min: 1, max: 30, step: 1, initial: 2 },
  apiCalls: { min: 0, max: 500_000, step: 1_000, initial: 20_000 },
} as const

export type CalcInput = { skus: number; users: number; warehouses: number; apiCalls: number }

export interface Estimate {
  // True when the whole operation fits the free plan, which then costs $0.
  fitsFree: boolean
  base: number
  lines: { key: keyof CalcInput; extraUnits: number; blocks: number; amount: number }[]
  total: number
}

function blocksOver(value: number, included: number, per: number): { extraUnits: number; blocks: number } {
  const extraUnits = Math.max(0, Math.round(value) - included)
  return { extraUnits, blocks: Math.ceil(extraUnits / per) }
}

export function estimate(input: CalcInput): Estimate {
  // A month is counted as 30 days of the free plan's daily API ceiling. The
  // real ceiling is per day, which the page says next to the result.
  const fitsFree =
    input.skus <= FREE_PLAN.skus &&
    input.users <= FREE_PLAN.users &&
    input.warehouses <= FREE_PLAN.warehouses &&
    input.apiCalls <= FREE_PLAN.apiCallsPerDay * 30

  const { included, addOns } = FULL_PLAN
  const parts: [keyof CalcInput, number, { per: number; price: number }][] = [
    ['skus', included.skus, addOns.skus],
    ['users', included.users, addOns.users],
    ['warehouses', included.warehouses, addOns.warehouses],
    ['apiCalls', included.apiCallsPerMonth, addOns.apiCalls],
  ]
  const lines = parts.map(([key, inc, { per, price }]) => {
    const { extraUnits, blocks } = blocksOver(input[key], inc, per)
    return { key, extraUnits, blocks, amount: blocks * price }
  })
  const total = FULL_PLAN.baseMonthly + lines.reduce((sum, l) => sum + l.amount, 0)
  return { fitsFree, base: FULL_PLAN.baseMonthly, lines, total }
}
