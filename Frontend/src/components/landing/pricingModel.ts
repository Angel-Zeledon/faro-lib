// PRICES — premium revision confirmed by the owner 2026-10-02. The
// owner asked for a premium position ("inflate the prices a little"); these
// replace the 2026-10-01 set ($39 base; $10 / $5 / $8 / $2 add-ons). The base
// still prices an API block at the same $ per 1,000 monthly calls; what changed
// on 2026-10-05 is that the API is a PAID feature (the free plan has none) and
// the Full plan is limited, so its API allowance is stated per day.
//
// Every number the pricing calculator shows lives in this file, and nowhere
// else. The landing renders an ESTIMATE from it: there is still no checkout,
// no card and no Stripe (CLAUDE.md). A tenant becomes `paid` because somebody
// talked to us and `tenants.tier` was set; the final price is agreed in that
// conversation, and this model only tells a visitor what to expect before it.
//
// The ceilings below MUST match backend/entitlements/plans.py (FREE, PAID).
// They are repeated here, not imported, because the landing cannot reach the
// backend's Python — if plans.py moves, move these with it.

export const CURRENCY = 'USD'

// ── Which machine-facing channels each plan includes (owner, 2026-10-05) ─────
// The API, the MCP server and the WhatsApp bot start at the Full plan; the free
// plan and the trial account do not include them. Mirrors the `features` map of
// GET /entitlements (plans.py api_access / mcp_access / whatsapp_bot).
export const PLAN_FEATURES = {
  free: { api: false, mcp: false, whatsappBot: false },
  full: { api: true, mcp: true, whatsappBot: true },
  corporate: { api: true, mcp: true, whatsappBot: true },
} as const

// ── Free: permanent, short ceilings, no API / MCP / bot (plans.py FREE) ──────
export const FREE_PLAN = {
  skus: 100,
  users: 2,
  warehouses: 1,
  apiCallsPerDay: 0,
} as const

// A month is counted as 30 days wherever a per-day allowance meets a monthly
// add-on price.
export const DAYS_PER_MONTH = 30

// ── Full: a monthly base plus what goes past what the base includes ─────────
// First plan with the API, MCP and the WhatsApp bot, and still limited
// (plans.py PAID: 500 SKUs, 3 users, 2 warehouses, 2,000 API calls a day).
export const FULL_PLAN = {
  baseMonthly: 59,
  included: {
    skus: 500,
    users: 3,
    warehouses: 2,
    apiCallsPerDay: 2_000,
  },
  // Each add-on is priced per block: `price` for every started `per` units
  // above what the base includes. API blocks are 1,000 calls a MONTH (the
  // price per call is unchanged); the calculator takes calls per DAY and
  // converts with DAYS_PER_MONTH.
  addOns: {
    skus: { per: 500, price: 12 },
    users: { per: 1, price: 7 },
    warehouses: { per: 1, price: 10 },
    apiCalls: { per: 1_000, price: 2.5 },
  },
} as const

// ── Corporate: a position for large accounts, quoted in a conversation ───────
// Added by the owner's decision of 2026-10-05: a corporate band starting at
// $890 a month on an ANNUAL contract, aimed at companies that order and buy
// months or years ahead. It has everything the Full plan has, with the
// ceilings lifted and agreed in the quote (plans.py CORPORATE); nothing here
// is a quantity, so no included amount is invented. `from` semantics: this is where the
// quote starts, never a list price; there is no checkout. Deliberately NOT
// used by estimate(): the calculator prices the full plan only.
export const CORPORATE_PLAN = {
  baseMonthly: 890,
  billing: 'annual-contract',
  from: true,
} as const

// The ranges the calculator's sliders cover. A number typed into the box can
// go past the slider's end; the estimate keeps counting.
export const CALC_RANGES = {
  skus: { min: 0, max: 20_000, step: 100, initial: 2_500 },
  users: { min: 1, max: 50, step: 1, initial: 6 },
  warehouses: { min: 1, max: 30, step: 1, initial: 2 },
  // Calls per DAY (the unit of the plan's ceiling). Zero is a valid answer: the
  // free plan has no API.
  apiCalls: { min: 0, max: 20_000, step: 100, initial: 1_000 },
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
  // The free plan has no API, so any API use means the Full plan.
  const fitsFree =
    input.skus <= FREE_PLAN.skus &&
    input.users <= FREE_PLAN.users &&
    input.warehouses <= FREE_PLAN.warehouses &&
    input.apiCalls <= FREE_PLAN.apiCallsPerDay

  const { included, addOns } = FULL_PLAN
  const parts: [keyof CalcInput, number, { per: number; price: number }][] = [
    ['skus', included.skus, addOns.skus],
    ['users', included.users, addOns.users],
    ['warehouses', included.warehouses, addOns.warehouses],
    ['apiCalls', included.apiCallsPerDay, addOns.apiCalls],
  ]
  const lines = parts.map(([key, inc, { per, price }]) => {
    if (key === 'apiCalls') {
      // Shown per day (the unit the plan is stated in), priced per monthly block.
      const extraPerDay = Math.max(0, Math.round(input.apiCalls) - inc)
      const blocks = Math.ceil((extraPerDay * DAYS_PER_MONTH) / per)
      return { key, extraUnits: extraPerDay, blocks, amount: blocks * price }
    }
    const { extraUnits, blocks } = blocksOver(input[key], inc, per)
    return { key, extraUnits, blocks, amount: blocks * price }
  })
  const total = FULL_PLAN.baseMonthly + lines.reduce((sum, l) => sum + l.amount, 0)
  return { fitsFree, base: FULL_PLAN.baseMonthly, lines, total }
}
