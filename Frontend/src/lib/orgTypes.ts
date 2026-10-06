// Organization hierarchy (a holding and its subsidiaries). Shapes of the
// Rust-only `/org/*` routes (backend-rs/src/routes/org*.rs).

export interface OrgTenant {
  tenant_id: string
  name: string
  label: string | null
  link_id: string | null
  is_own: boolean
  currency: string
}

export interface OrgUnavailable { link_id: string; label: string; reason: string }

export interface OrgOverview {
  entitled: boolean
  /** Why not entitled: `org_not_entitled` or `warehouse_scope_company_totals`. */
  reason: string | null
  can_manage: boolean
  tenants: OrgTenant[]
  unavailable: OrgUnavailable[]
}

export interface OrgLinkAsParent {
  id: string
  label: string
  status: 'pending' | 'active' | 'revoked'
  expired: boolean
  code_expires_at: string | null
  created_at: string
  accepted_at: string | null
  revoked_at: string | null
  revoked_side: 'parent' | 'child' | 'system' | null
  subsidiary_name: string | null
  grants: number
}

export interface OrgLinkAsChild {
  id: string
  status: 'pending' | 'active' | 'revoked'
  accepted_at: string | null
  revoked_at: string | null
  revoked_side: 'parent' | 'child' | 'system' | null
  parent_name: string | null
}

export interface OrgLinks { as_parent: OrgLinkAsParent[]; as_child: OrgLinkAsChild[] }

export interface OrgCreatedLink { id: string; label: string; status: 'pending'; code: string; expires_at: string }

export interface OrgMember {
  user_id: string
  email: string
  full_name: string | null
  status: string
  granted_at: string
}

export interface OrgMoney { currency: string; amount: number }

interface OrgRowBase { tenant_id: string; name: string; is_own: boolean }

export interface OrgCommittedDemand {
  tenants: (OrgRowBase & {
    commitments: number; skus: number; quantity: number; weighted_quantity: number
    added_to_forecast_quantity: number; overdue: number
    earliest_delivery: string | null; latest_delivery: string | null
  })[]
  totals: { commitments: number; quantity: number; weighted_quantity: number; overdue: number }
  months: { month: string; commitments: number; quantity: number; weighted_quantity: number }[]
  unavailable: OrgUnavailable[]
}

export type OrgStockState = 'counted' | 'stale' | 'inconsistent' | 'missing'

export interface OrgStockSignals {
  tenants: (OrgRowBase & {
    state: OrgStockState; counted: boolean
    computed_at?: string; skus?: number; signals?: Record<string, number>
    inventory_value?: OrgMoney[]; skus_without_value?: number
  })[]
  totals: {
    skus: number; signals: Record<string, number>; inventory_value: OrgMoney[]
    skus_without_value: number; tenants_counted: number; tenants_excluded: number
  }
  excluded: { tenant_id: string; name: string; reason: OrgStockState }[]
  unavailable: OrgUnavailable[]
}

export interface OrgPurchaseOrders {
  window_days: number
  tenants: (OrgRowBase & {
    orders: number; cancelled: number; active: number; sent: number; not_sent: number; paid: number
    awaiting_payment: number; awaiting_reception: number; value: OrgMoney[]; orders_without_value: number
  })[]
  totals: {
    orders: number; cancelled: number; active: number; sent: number; not_sent: number; paid: number
    awaiting_payment: number; awaiting_reception: number; value: OrgMoney[]; orders_without_value: number
  }
  unavailable: OrgUnavailable[]
}

export interface OrgBudgetLine {
  root_id: string; period_type: string; period_start: string; period_end: string
  currency: string; amount: number; spent: number; committed: number; ordered: number
  remaining: number; over_budget: boolean; hard_cap: boolean
  unknown_cost_lines: number; currency_mismatch: boolean
}

export interface OrgBudgets {
  tenants: (OrgRowBase & { budgets: OrgBudgetLine[]; other_scope_budgets_not_included: number })[]
  totals: {
    by_currency: { currency: string; amount: number; spent: number; committed: number; remaining: number }[]
    unknown_cost_lines: number; budgets_not_totalled: number
  }
  unavailable: OrgUnavailable[]
}
