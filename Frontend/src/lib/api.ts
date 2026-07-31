import type {
  SessionInfo, DatasetMeta, DataProfile, ColumnOptions, InspectionResult,
  QualityReport, RunWarnings, ConfigSchema, ChooseColumnsBody, CanonicalColumnsBody,
  JobResponse, MetricsResponse, InventoryResponse, RoutingPlan, TrainingResults,
  ForecastSeries, DataHealthReport,
  Chat, ChatMessage, MessagesPage, ChatSourceType,
  DataSource, DataPreview, EditableTable, SqlQueryResult, SqlEngine,
  InventoryStock, InventoryStatusResponse, InventoryDashboardSummary,
  InventoryEvent, InventoryROISummary, POLogEntry, POLineDecision,
  CalendarCatalogResponse, CalendarSeedResult, EventMultiplier,
  Supplier, SkuSupplier, MorningBriefing, DeadStockResponse, OptimizationResponse,
  ShrinkageReason, ShrinkageRecord,
  Warehouse, WarehouseStatusResponse, Transfer, TransferLane,
  PlanningState, PlanningPeriod, MeUser,
} from './types'
import { getToken, clearAuth, tryRefresh } from './auth'

const BASE = '/api'

let _redirectingToLogin = false

// ── Centralised error interceptor (feature 2.6) ───────────────────────────────
// Every failing request used to be handled — or silently swallowed — screen by
// screen, so the same HTTP 403 could show up as a red inline box on one page,
// a raw "HTTP 403" on another, and nothing at all on a third. Everything now
// funnels through `request()` and comes out as an `ApiError` carrying a stable
// `kind`. The UI layer maps that kind to copy (`components/ui/States.tsx`) and
// to a standard toast (`components/layout/ApiErrorBridge.tsx`), so the wording
// for a permission failure lives in exactly one place.

export type ApiErrorKind =
  | 'session'     // 401 after a failed silent refresh — the user is logged out
  | 'permission'  // 403 — authenticated but the role is not allowed
  | 'notfound'    // 404 — the resource is gone or never existed
  | 'validation'  // 400 / 409 / 422 — the request itself was rejected
  | 'server'      // 5xx — the backend broke
  | 'network'     // fetch never completed: offline, DNS, CORS, backend down

/**
 * One failed field from a FastAPI/Pydantic 422. `type` is Pydantic's stable
 * machine name for the rule that failed ('greater_than', 'missing', …) and
 * `ctx` carries its bounds — together they let the UI build a Spanish sentence
 * instead of showing Pydantic's English `msg` verbatim.
 */
export interface FieldError {
  field: string
  type:  string
  ctx:   Record<string, unknown>
  msg:   string          // English original, used as a last-resort fallback
}

export class ApiError extends Error {
  readonly kind:   ApiErrorKind
  readonly status: number     // 0 when the request never reached the server
  readonly detail: string     // backend-provided reason (English fallback), '' when none
  readonly path:   string
  /** Per-field validation failures on a 422; empty for every other error. */
  readonly fieldErrors: FieldError[]
  // Stable machine error code from the backend's error envelope (`error_code`),
  // '' when the backend didn't send one. The UI maps it to a localized
  // (Spanish/English) `errors.<code>` string, interpolating `params`, and only
  // falls back to `detail` when the code is absent or unmapped. This is what
  // keeps backend errors English while the user still reads Spanish.
  readonly code:   string
  readonly params: Record<string, unknown>

  constructor(
    kind: ApiErrorKind, status: number, detail: string, path: string,
    code = '', params: Record<string, unknown> = {},
    fieldErrors: FieldError[] = [],
  ) {
    // `message` stays the most specific text available so existing
    // `e.message` call sites (and console traces) keep working.
    super(detail || `HTTP ${status}`)
    this.name   = 'ApiError'
    this.kind   = kind
    this.status = status
    this.detail = detail
    this.path   = path
    this.code   = code
    this.params = params
    this.fieldErrors = fieldErrors
  }
}

function kindForStatus(status: number): ApiErrorKind {
  if (status === 401) return 'session'
  if (status === 403) return 'permission'
  if (status === 404) return 'notfound'
  if (status === 400 || status === 409 || status === 422) return 'validation'
  if (status >= 500) return 'server'
  return 'validation'
}

/** Narrow an unknown catch value to an ApiError. */
export const isApiError = (e: unknown): e is ApiError => e instanceof ApiError

type ApiErrorNotifier = (err: ApiError) => void
let _notifier: ApiErrorNotifier | null = null

/**
 * Registered once by `ApiErrorBridge` so the non-React api layer can raise a
 * toast. Returns an unsubscribe so React strict-mode double-mounts don't leave
 * a stale notifier behind.
 */
export function setApiErrorNotifier(fn: ApiErrorNotifier): () => void {
  _notifier = fn
  return () => { if (_notifier === fn) _notifier = null }
}

function notify(err: ApiError, silent: boolean) {
  // A lost session already redirects to /login — a toast on a page that is
  // being torn down would only flash.
  if (silent || err.kind === 'session') return
  _notifier?.(err)
}

/**
 * Per-call interceptor overrides. `silent: true` suppresses the automatic
 * toast — used by callers that render the failure themselves (a full-screen
 * `ErrorState`) so the user isn't told the same thing twice.
 */
export interface RequestOpts { silent?: boolean }

// FastAPI validation errors send `detail` as an array of {type, loc, msg, ...}
// instead of a string. Without this, `new Error(detail)` stringifies the array
// to "[object Object]" and the UI shows that literal text to the user.
function extractErrorMessage(err: unknown): string | undefined {
  const detail = (err as { detail?: unknown })?.detail
  if (typeof detail === 'string') return detail
  if (Array.isArray(detail)) {
    return detail
      .map((e: { loc?: unknown[]; msg?: string }) => {
        const field = Array.isArray(e?.loc) ? e.loc[e.loc.length - 1] : undefined
        return field ? `${field}: ${e.msg}` : e.msg
      })
      .filter(Boolean)
      .join('; ')
  }
  return (err as { error?: { message?: string } })?.error?.message
}

// The backend's AppError envelope adds `error_code` (a stable machine code) and
// `error_params` (the dynamic values) alongside `detail`. Pull them out so the
// UI can render the localized `errors.<code>` string instead of the English
// `detail`. Absent on plain FastAPI errors — the code stays '' and the UI
// simply falls back to `detail`.
/**
 * Pull the structured field failures out of a FastAPI 422 body. Keeping
 * Pydantic's `type` + `ctx` (instead of only its English `msg`) is what lets
 * `useErrorDetail` render "La cantidad no puede ser mayor que 1.000.000.000"
 * rather than "qty: Input should be less than or equal to 1000000000".
 */
function extractFieldErrors(err: unknown): FieldError[] {
  const detail = (err as { detail?: unknown })?.detail
  if (!Array.isArray(detail)) return []
  return detail.flatMap((e: { loc?: unknown[]; msg?: string; type?: string; ctx?: unknown }) => {
    if (typeof e?.type !== 'string') return []
    // loc looks like ['body', 'lines', 0, 'qty'] — the last string is the field.
    const parts = Array.isArray(e.loc) ? e.loc.filter(p => typeof p === 'string') : []
    const field = parts.length ? String(parts[parts.length - 1]) : ''
    return [{
      field,
      type: e.type,
      ctx: (e.ctx && typeof e.ctx === 'object' ? e.ctx : {}) as Record<string, unknown>,
      msg: typeof e.msg === 'string' ? e.msg : '',
    }]
  })
}

function extractErrorCode(err: unknown): string {
  const code = (err as { error_code?: unknown })?.error_code
  return typeof code === 'string' ? code : ''
}

function extractErrorParams(err: unknown): Record<string, unknown> {
  const params = (err as { error_params?: unknown })?.error_params
  return params && typeof params === 'object' ? (params as Record<string, unknown>) : {}
}

function _doFetch(method: string, path: string, body?: unknown): Promise<Response> {
  const isForm = body instanceof FormData
  const token  = getToken()

  const headers: Record<string, string> = isForm ? {} : { 'Content-Type': 'application/json' }
  if (token) headers['Authorization'] = `Bearer ${token}`

  return fetch(`${BASE}${path}`, {
    method,
    headers,
    body: isForm ? body : body !== undefined ? JSON.stringify(body) : undefined,
  })
}

function _sessionLost(): never {
  if (!_redirectingToLogin) {
    _redirectingToLogin = true
    clearAuth()
    window.location.href = '/login'
  }
  throw new Error('Session expired')
}

async function request<T = unknown>(
  method: string, path: string, body?: unknown, opts: RequestOpts = {},
): Promise<T> {
  const silent = opts.silent === true

  let res: Response
  try {
    res = await _doFetch(method, path, body)
  } catch {
    // fetch() only rejects when the request never completed: offline, DNS
    // failure, or the backend not listening. Any HTTP status resolves.
    const err = new ApiError('network', 0, '', path)
    notify(err, silent)
    throw err
  }

  if (res.status === 401) {
    // Auth endpoints return 401 for wrong credentials/tokens — surface that
    // error to the form instead of treating it as an expired session.
    if (path.startsWith('/auth/')) {
      const payload = await res.json().catch(() => ({ detail: res.statusText }))
      // `detail` stays whatever the backend said (English) — the auth screens
      // map the 401 to their own localized copy rather than rendering it.
      const err = new ApiError(
        'validation', 401, extractErrorMessage(payload) || '', path,
        extractErrorCode(payload), extractErrorParams(payload),
      )
      notify(err, silent)
      throw err
    }
    // Expired access token: renew silently with the refresh token and retry
    // once, so a 15-minute token never kicks the user back to /login mid-task.
    // `_sessionLost()` never returns — it clears auth and redirects.
    if (await tryRefresh()) {
      res = await _doFetch(method, path, body)
      if (res.status === 401) _sessionLost()
    } else {
      _sessionLost()
    }
  }

  if (!res.ok) {
    const payload = await res.json().catch(() => ({ detail: res.statusText }))
    const err = new ApiError(
      kindForStatus(res.status), res.status, extractErrorMessage(payload) || '', path,
      extractErrorCode(payload), extractErrorParams(payload),
      extractFieldErrors(payload),
    )
    notify(err, silent)
    throw err
  }

  // 204 No Content has no body at all: res.json() would reject with a parse
  // error and surface as a fake failure to callers of DELETE endpoints.
  if (res.status === 204) return undefined as T

  // Backend wraps responses as { success, data, meta } — unwrap automatically
  const json = await res.json()
  return (json?.data !== undefined ? json.data : json) as T
}

// Binary file download — triggers browser save dialog
async function downloadBlob(path: string, filename: string): Promise<void> {
  const fetchBlob = () => {
    const token = getToken()
    return fetch(`${BASE}${path}`, {
      headers: token ? { Authorization: `Bearer ${token}` } : {},
    })
  }
  let res = await fetchBlob()
  if (res.status === 401) {
    if (await tryRefresh()) {
      res = await fetchBlob()
      if (res.status === 401) _sessionLost()
    } else {
      _sessionLost()
    }
  }
  if (!res.ok) {
    const payload = await res.json().catch(() => ({ detail: res.statusText }))
    const err = new ApiError(
      kindForStatus(res.status), res.status, extractErrorMessage(payload) || '', path,
      extractErrorCode(payload), extractErrorParams(payload),
      extractFieldErrors(payload),
    )
    notify(err, false)
    throw err
  }
  const blob = await res.blob()
  const url  = URL.createObjectURL(blob)
  const a    = document.createElement('a')
  a.href = url; a.download = filename; a.click()
  URL.revokeObjectURL(url)
}

// POST variant: for downloads whose request carries a body (e.g. a SQL query
// too long for a URL). Same auth/refresh/error handling as downloadBlob.
async function downloadBlobPost(path: string, body: unknown, filename: string): Promise<void> {
  const fetchBlob = () => {
    const token = getToken()
    return fetch(`${BASE}${path}`, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        ...(token ? { Authorization: `Bearer ${token}` } : {}),
      },
      body: JSON.stringify(body),
    })
  }
  let res = await fetchBlob()
  if (res.status === 401) {
    if (await tryRefresh()) {
      res = await fetchBlob()
      if (res.status === 401) _sessionLost()
    } else {
      _sessionLost()
    }
  }
  if (!res.ok) {
    const payload = await res.json().catch(() => ({ detail: res.statusText }))
    const err = new ApiError(
      kindForStatus(res.status), res.status, extractErrorMessage(payload) || '', path,
      extractErrorCode(payload), extractErrorParams(payload),
      extractFieldErrors(payload),
    )
    notify(err, false)
    throw err
  }
  const blob = await res.blob()
  const url  = URL.createObjectURL(blob)
  const a    = document.createElement('a')
  a.href = url; a.download = filename; a.click()
  URL.revokeObjectURL(url)
}

// ── Auth ──────────────────────────────────────────────────────────────────────
export const authSignup = (body: {
  email: string; password: string; full_name?: string; tenant_name: string
  /** E.164, required — purchase orders are delivered here for forwarding. */
  whatsapp_number: string
}) =>
  request<{
    user: Record<string, unknown>; tenant: Record<string, unknown>
    /** False when no mail transport accepted the message. */
    email_sent: boolean
    /** Stable code for WHY it failed, localized by the UI. */
    email_error: string | null
    /** Present only when `email_sent` is false — the link is shown on screen
     *  rather than sending the user to an inbox that received nothing. */
    verify_url: string | null
  }>('POST', '/auth/signup', body)

export const authLogin = (email: string, password: string) =>
  request<{
    access_token:  string
    refresh_token: string
    token_type:    string
    expires_in:    number
    user: {
      id: string; email: string; full_name: string | null; role: string
      tenant_id: string
      /** Unverified users log in fine — only outward actions (invites,
       *  integrations, sending notifications) demand verification. */
      email_verified: boolean
    }
  }>('POST', '/auth/login', { email, password })

export const authVerifyEmail = (token: string) =>
  request<{ message: string }>('POST', '/auth/verify-email', { token })

/** Deliberately generic response — identical for unknown, verified and resent
 *  addresses, so it cannot be used to enumerate accounts. */
export const authResendVerification = (email: string) =>
  request<{ message: string }>('POST', '/auth/resend-verification', { email })

export const authForgotPassword = (email: string) =>
  request<{ message: string }>('POST', '/auth/forgot-password', { email })

export const authForgotPasswordVerify = (email: string, code: string) =>
  request<{ reset_token: string }>('POST', '/auth/forgot-password/verify', { email, code })

export const authResetPassword = (token: string, new_password: string) =>
  request<{ message: string }>('POST', '/auth/reset-password', { token, new_password })

export const authLogout = () =>
  request<{ message: string }>('POST', '/auth/logout')

// ── Sessions ──────────────────────────────────────────────────────────────────
export const getSessions   = () =>
  request<{ items: SessionInfo[]; total: number }>('GET', '/sessions')
    .then(r => (Array.isArray(r) ? r : r.items) ?? [])
// Enriched history list: dataset name, horizon, SKU count, granularity.
export const getSessionSummaries = (skip = 0, limit = 100) =>
  request<{ items: import('./types').SessionSummary[]; total: number }>(
    'GET', `/sessions/summary?skip=${skip}&limit=${limit}`,
  )
export const getSession    = (id: string)    => request<SessionInfo>('GET', `/sessions/${id}`)
export const createSession = (name?: string) =>
  request<SessionInfo>('POST', '/sessions', {
    name: name || `session-${new Date().toISOString().slice(0, 16).replace('T', '-')}`,
  })
export const patchSession  = (id: string, body: Record<string, unknown>) =>
  request<SessionInfo>('PATCH', `/sessions/${id}`, body)
export const deleteSession = (id: string) =>
  fetch(`${BASE}/sessions/${id}`, {
    method: 'DELETE',
    headers: { Authorization: `Bearer ${getToken()}` },
  }).then(() => undefined as void)

// ── Datasets ──────────────────────────────────────────────────────────────────
export const uploadDataset = (fd: FormData) =>
  request<DatasetMeta>('POST', '/datasets', fd)

// Newest-first metadata of the tenant's uploaded datasets — powers the
// quick-start "use an existing dataset" tab.
export const listDatasets = (skip = 0, limit = 50) =>
  request<{ items: DatasetMeta[]; total: number }>('GET', `/datasets?skip=${skip}&limit=${limit}`)

export const attachDataset = (sessionId: string, dataset_id: string) =>
  request<SessionInfo>('POST', `/sessions/${sessionId}/dataset`, { dataset_id })

export const inspectSession = (id: string) =>
  request<InspectionResult>('GET', `/sessions/${id}/inspect`)

export const getQuality     = (id: string) => request<QualityReport>('GET', `/sessions/${id}/quality`)
export const getRunWarnings = (id: string) => request<RunWarnings>('GET', `/sessions/${id}/warnings`)
export const getDataHealth  = (id: string, refresh = false) =>
  request<DataHealthReport>('GET', `/sessions/${id}/health${refresh ? '?refresh=true' : ''}`)

// ── Compat helpers (used by data/page) ───────────────────────────────────────
export const uploadFile = (_sessionId: string, fd: FormData) => uploadDataset(fd)
export const getProfile = (id: string) => inspectSession(id).then(r => r.profile)
export const getColumns = (id: string) => inspectSession(id).then(r => r.column_options)

// ── Configuration ─────────────────────────────────────────────────────────────
export const chooseColumns = (id: string, body: ChooseColumnsBody) =>
  request<{ ok: boolean }>('POST', `/sessions/${id}/configure/columns`, body)

export const chooseColumnsCanonical = (id: string, body: CanonicalColumnsBody) =>
  request<{ ok: boolean }>('POST', `/sessions/${id}/configure/columns`, body)

export const setFeatures = (id: string, body: Record<string, unknown>) =>
  request<{ ok: boolean }>('POST', `/sessions/${id}/configure/features`, body)

// ── Pre-training data gate ────────────────────────────────────────────────────
// What is wrong with this file and what the user may do about it. Evaluated
// against the CONFIRMED mapping, so this is the same verdict `POST /train`
// enforces — asking here and being refused there would be the worst of both.
export const getDataGate = (id: string, opts?: { silent?: boolean }) =>
  request<import('./types').DataGate>('GET', `/sessions/${id}/data-gate`, undefined, opts)

// {issue_type: option_code}. Validated against the live gate, so a stale choice
// for a finding this file no longer has is rejected rather than stored.
export const setRemediations = (id: string, remediations: Record<string, string>) =>
  request<{ remediations: Record<string, string> }>(
    'POST', `/sessions/${id}/configure/remediations`, { remediations },
  )

export const setModels = (
  id: string,
  selected_models: string[],
  hyperparameters?: Record<string, Record<string, unknown>>,
) =>
  request<{ ok: boolean }>('POST', `/sessions/${id}/configure/models`, {
    mode: 'selected',
    selected_models,
    hyperparameters: hyperparameters ?? {},
    auto_select_best: true,
    selection_metric: 'wape',
  })

export const setValidationConfig = (id: string, body: Record<string, unknown>) =>
  request<{ ok: boolean }>('POST', `/sessions/${id}/configure/validation`, body)

export const setForecastConfig = (id: string, body: Record<string, unknown>) =>
  request<{ ok: boolean }>('POST', `/sessions/${id}/config/forecast`, body)

export const setBusinessConfig = (id: string, body: Record<string, unknown>) =>
  request<{ ok: boolean }>('POST', `/sessions/${id}/config/business`, body)

export const getConfigSchema = (id: string) =>
  request<ConfigSchema>('GET', `/sessions/${id}/config-schema`)

export const getColumnsConfig     = (id: string) => request<Record<string, unknown>>('GET', `/sessions/${id}/configure/columns`)
export const getFeaturesConfig    = (id: string) => request<Record<string, unknown>>('GET', `/sessions/${id}/configure/features`)
export const getSavedModelsConfig = (id: string) => request<Record<string, unknown>>('GET', `/sessions/${id}/configure/models`)
// ── Training ──────────────────────────────────────────────────────────────────
// A training launch fans out into a granularity family (daily/weekly/monthly),
// one session + job per grain. `sessions` is finest-first; `sessions[0]` is the
// base (finest) grain and its `job_id` equals `base_job_id`. May be absent/empty
// for family-less responses, in which case the caller polls just `job_id`.
export interface TrainingFamilyMember {
  session_id:  string
  granularity: string
  job_id:      string
}
export interface TrainingFamily {
  family_id:   string
  base_job_id: string
  sessions:    TrainingFamilyMember[]
}

// Launch preferences from the Quick Start wizard: horizon in calendar days and
// the planning grain ('auto' = backend fans out into every viable grain).
export interface TrainLaunchOptions {
  user_horizon_days?: number
  user_granularity?: 'auto' | 'daily' | 'weekly' | 'monthly'
}

export const startTraining = (id: string, opts?: TrainLaunchOptions) =>
  request<{ job_id: string; status: string; family?: TrainingFamily }>('POST', `/sessions/${id}/train`, opts)

// One-click demo: seeds dataset + configs + stock and queues training
export const startDemoQuickstart = (opts?: TrainLaunchOptions & { name?: string }) =>
  request<{ session_id: string; job_id: string; dataset_id: string; stock_seeded: string[]; family?: TrainingFamily }>(
    'POST', '/demo/quickstart', opts,
  )

export const getJob = (job_id: string) =>
  request<JobResponse>('GET', `/jobs/${job_id}`)

export const getJobLogs = (job_id: string) =>
  request<{ job_id: string; lines: string[]; total: number }>('GET', `/jobs/${job_id}/logs`)

// ── Results ───────────────────────────────────────────────────────────────────
export const getMetrics = (id: string) =>
  request<MetricsResponse>('GET', `/sessions/${id}/metrics`)

export const getInventory = (id: string) =>
  request<InventoryResponse>('GET', `/sessions/${id}/inventory`)

/**
 * The whole stored training result in one call.
 *
 * `/metrics` and `/inventory` are slices of exactly this payload, so a screen
 * that needs a third slice (the policy backtest, the demand-risk bands) is
 * better off asking once than asking three times and downloading the metric
 * rows twice.
 */
export const getTrainingResults = (id: string) =>
  request<TrainingResults>('GET', `/sessions/${id}/results`)

// ── AI Analyst ────────────────────────────────────────────────────────────────
export const analystQuery = (
  sessionId: string,
  question: string,
  sku?: string,
  history?: { role: 'user' | 'assistant'; content: string }[],
) =>
  request<{ question: string; answer: string; sku: string | null; source: string; retrieved_count: number }>(
    'POST', `/sessions/${sessionId}/analyst/query`, { question, sku, history },
  )


// ── Data Sources ──────────────────────────────────────────────────────────────
export const listDataSources = (skip = 0, limit = 50) =>
  request<{ items: DataSource[]; total: number }>('GET', `/data-sources?skip=${skip}&limit=${limit}`)

export const getDataSource = (id: string) =>
  request<DataSource>('GET', `/data-sources/${id}`)

export const createFileSource = (fd: FormData) =>
  request<DataSource>('POST', '/data-sources/file', fd)

export const createSqlSource = (body: {
  name: string; description?: string
  host: string; port: number; database: string
  username: string; password: string; engine: SqlEngine
}) => request<DataSource>('POST', '/data-sources/sql', body)

export const replaceFileSource = (id: string, fd: FormData) =>
  request<DataSource>('POST', `/data-sources/${id}/file`, fd)

export const updateSqlConfig = (id: string, body: {
  host: string; port: number; database: string
  username: string; password?: string; engine: SqlEngine
}) => request<DataSource>('PATCH', `/data-sources/${id}/sql-config`, body)

export const testSqlConnection = (id: string) =>
  request<{ ok: boolean; status: string; error?: string }>('POST', `/data-sources/${id}/test-connection`)

export const executeSqlQuery = (id: string, sql: string, limit = 500) =>
  request<SqlQueryResult>('POST', `/data-sources/${id}/execute-query`, { sql, limit })

export const saveSqlQuery = (id: string, sql: string) =>
  request<DataSource>('PATCH', `/data-sources/${id}/query`, { sql })

export const materializeSqlSource = (id: string, body: { sql?: string; name?: string }) =>
  request<DataSource>('POST', `/data-sources/${id}/materialize`, body)

export const exportSqlQueryXlsx = (id: string, sql: string, filename: string) =>
  downloadBlobPost(`/data-sources/${id}/export-query`, { sql }, filename)

export const getDataSourcePreview = (id: string, rows = 100, sheet?: string) =>
  request<DataPreview>('GET', `/data-sources/${id}/preview?rows=${rows}${sheet ? `&sheet=${encodeURIComponent(sheet)}` : ''}`)

export const getEditableTable = (id: string) =>
  request<EditableTable>('GET', `/data-sources/${id}/edit-table`)

export const saveDatasetAsNew = (id: string, body: {
  name?: string; columns: string[]; rows: Record<string, unknown>[]
}) => request<DataSource>('POST', `/data-sources/${id}/save-as-new`, body)

export const renameDataSource = (id: string, name: string, description?: string) =>
  request<DataSource>('PATCH', `/data-sources/${id}`, { name, description })

export const deleteDataSource = (id: string) =>
  request<{ deleted: string }>('DELETE', `/data-sources/${id}`)

// ── AI Analyst Persistent Chats ───────────────────────────────────────────────
export const listChats   = (search?: string) =>
  request<Chat[]>('GET', `/analyst/chats${search ? `?search=${encodeURIComponent(search)}` : ''}`)

export const createChat  = (body: { session_id?: string; title?: string; data_sources?: string[] } = {}) =>
  request<Chat>('POST', '/analyst/chats', body)

export const updateChat  = (chatId: string, body: Partial<Pick<Chat, 'title' | 'is_favorite' | 'session_id' | 'data_sources'>>) =>
  request<Chat>('PATCH', `/analyst/chats/${chatId}`, body)

export const deleteChat  = (chatId: string) =>
  request<{ deleted: boolean }>('DELETE', `/analyst/chats/${chatId}`)

export const getChatMessages = (chatId: string, limit = 30, before?: string) =>
  request<MessagesPage>('GET', `/analyst/chats/${chatId}/messages?limit=${limit}${before ? `&before=${before}` : ''}`)

export const sendChatMessage = (
  chatId: string,
  question: string,
  sessionId?: string | null,
  sku?: string | null,
) =>
  request<{ user_message: ChatMessage; ai_message: ChatMessage }>(
    'POST', `/analyst/chats/${chatId}/messages`,
    { question, session_id: sessionId, sku },
  )

export const getDataSourceTypes = () =>
  request<ChatSourceType[]>('GET', '/analyst/data-source-types')

// ── User Profile ─────────────────────────────────────────────────────────────
export const getMe = () =>
  request<MeUser>('GET', '/users/me')

export const updateMe = (body: { full_name?: string; whatsapp_number?: string }) =>
  request<Record<string, unknown>>('PATCH', '/users/me', body)

export const requestPasswordChange = (new_password: string) =>
  request<{ message: string }>('POST', '/users/me/change-password/request', { new_password })

export const confirmPasswordChange = (code: string, new_password: string) =>
  request<{ message: string }>('POST', '/users/me/change-password/confirm', { code, new_password })

// Start linking a WhatsApp number: stores it unverified and issues a 6-digit
// code. The backend delivers it over WhatsApp when Twilio is configured;
// otherwise (non-production only) it returns the code as `debug_code` so the
// in-app "type the code" flow works without a live round-trip. Re-calling acts
// as "resend" and is rate-limited (429 `whatsapp_code_resend_cooldown` with a
// `retry_after` param).
export const linkWhatsappNumber = (whatsapp_number: string) =>
  request<{ sent: boolean; debug_code?: string }>(
    'POST', '/users/me/whatsapp/link', { whatsapp_number },
  )

// Confirm the 6-digit code and mark the number verified.
export const confirmWhatsappNumber = (code: string) =>
  request<{ verified: boolean }>('POST', '/users/me/whatsapp/confirm', { code })

// ── Admin User Management ─────────────────────────────────────────────────────
export interface AdminUser {
  id: string
  email: string
  full_name: string | null
  role: string
  status: string
  email_verified: boolean
  created_at: string
  last_login_at: string | null
  tenant_id: string
}

export const listAdminUsers = (params?: {
  search?: string; status?: string; role?: string; limit?: number; offset?: number
}) => {
  const q = new URLSearchParams()
  if (params?.search) q.set('search', params.search)
  if (params?.status) q.set('status', params.status)
  if (params?.role)   q.set('role',   params.role)
  if (params?.limit  !== undefined) q.set('limit',  String(params.limit))
  if (params?.offset !== undefined) q.set('offset', String(params.offset))
  const qs = q.toString()
  return request<{ items: AdminUser[]; total: number }>('GET', `/users${qs ? `?${qs}` : ''}`)
}

export const createAdminUser = (body: { email: string; role: string; full_name?: string }) =>
  request<{ user: AdminUser }>('POST', '/users', body)

export const updateAdminUser = (id: string, body: { full_name?: string; role?: string; email?: string }) =>
  request<AdminUser>('PATCH', `/users/${id}`, body)

export const deleteAdminUser = (id: string) =>
  request<{ deleted: string }>('DELETE', `/users/${id}`)

export const setUserStatus = (id: string, status: string) =>
  request<AdminUser>('PATCH', `/users/${id}/status`, { status })

// ── Accuracy Tracking ─────────────────────────────────────────────────────────
export const getAccuracyReport = (sessionId: string, threshold?: number) =>
  request<import('./types').AccuracyReport>(
    'GET',
    `/sessions/${sessionId}/accuracy${threshold !== undefined ? `?threshold=${threshold}` : ''}`,
  )

export const uploadActuals = (sessionId: string, file: File) => {
  const fd = new FormData()
  fd.append('file', file)
  return request<{ matched_rows: number }>('POST', `/sessions/${sessionId}/reconcile`, fd)
}

// ── API Keys ──────────────────────────────────────────────────────────────────
export const createApiKey = (name: string) =>
  request<{ key: string; name: string }>('POST', '/api-keys', { name })

export const listApiKeys = () =>
  request<import('./types').ApiKey[]>('GET', '/api-keys')

export const revokeApiKey = (id: string) =>
  request<{ revoked: string }>('DELETE', `/api-keys/${id}`)

// ── Webhooks ──────────────────────────────────────────────────────────────────
export const createWebhook = (url: string, events: string[]) =>
  request<import('./types').Webhook>('POST', '/webhooks', { url, events })

export const listWebhooks = () =>
  request<import('./types').Webhook[]>('GET', '/webhooks')

export const deleteWebhook = (id: string) =>
  request<{ deleted: string }>('DELETE', `/webhooks/${id}`)

// ── Schedules ─────────────────────────────────────────────────────────────────
// "No schedule configured" is a legitimate state, not an error: ask silently
// and translate the 404 into `null` instead of letting it raise a toast.
export const getSchedule = (sessionId: string) =>
  request<import('./types').JobSchedule>('GET', `/sessions/${sessionId}/schedule`, undefined, { silent: true })
    .catch((e: unknown) => {
      if (isApiError(e) && e.kind === 'notfound') return null
      throw e
    })

export const saveSchedule = (sessionId: string, cronExpr: string, enabled: boolean) =>
  request<import('./types').JobSchedule>('POST', `/sessions/${sessionId}/schedule`, {
    cron_expr: cronExpr, enabled,
  })

export const deleteSchedule = (sessionId: string) =>
  request<{ deleted: string }>('DELETE', `/sessions/${sessionId}/schedule`)

export const getUserPermissions = (id: string) =>
  request<{ user_id: string; permissions: string[]; all_permissions: string[] }>('GET', `/users/${id}/permissions`)

// ── Production / BOM ──────────────────────────────────────────────────────────
export const getProductTypes = () =>
  request<Record<string, string>>('GET', '/inventory/product-types')

export const setProductType = (sku: string, productType: string) =>
  request<import('./types').InventoryStock>(
    'PATCH', `/inventory/stock/${encodeURIComponent(sku)}/product-type?product_type=${encodeURIComponent(productType)}`
  )

export const getBOM = (parentSku: string) =>
  request<import('./types').BomItem[]>('GET', `/inventory/bom/${encodeURIComponent(parentSku)}`)

export const upsertBOMItem = (
  parentSku: string, childSku: string,
  body: { quantity: number; unit?: string; notes?: string }
) =>
  request<import('./types').BomItem>(
    'PUT', `/inventory/bom/${encodeURIComponent(parentSku)}/${encodeURIComponent(childSku)}`, body
  )

export const deleteBOMItem = (parentSku: string, childSku: string) =>
  fetch(`${BASE}/inventory/bom/${encodeURIComponent(parentSku)}/${encodeURIComponent(childSku)}`, {
    method: 'DELETE', headers: { Authorization: `Bearer ${getToken()}` },
  }).then(() => undefined as void)

export const getWhereUsed = (childSku: string) =>
  request<{ parent_sku: string; parent_name: string | null; quantity: number }[]>(
    'GET', `/inventory/bom/${encodeURIComponent(childSku)}/used-in`
  )

export const getProductionRequirements = (sessionId: string, horizonDays = 30) =>
  request<import('./types').ProductionPlan>(
    'GET', `/inventory/production-requirements?session_id=${sessionId}&horizon_days=${horizonDays}`
  )

// ── Inventory ─────────────────────────────────────────────────────────────────
export const listInventoryStock = () =>
  request<InventoryStock[]>('GET', '/inventory/stock')

export const upsertInventoryStock = (sku: string, body: Partial<InventoryStock>) =>
  request<InventoryStock>('PUT', `/inventory/stock/${encodeURIComponent(sku)}`, body)

export const patchInventoryStock = (sku: string, body: Partial<InventoryStock>) =>
  request<InventoryStock>('PATCH', `/inventory/stock/${encodeURIComponent(sku)}`, body)

export const deleteInventoryStock = (sku: string) =>
  fetch(`${BASE}/inventory/stock/${encodeURIComponent(sku)}`, {
    method: 'DELETE',
    headers: { Authorization: `Bearer ${getToken()}` },
  }).then(() => undefined as void)

export const getInventoryStatus = (sessionId: string, serviceLevel = 0.95, opts?: RequestOpts) =>
  request<InventoryStatusResponse>(
    'GET',
    `/inventory/status?session_id=${sessionId}&service_level=${serviceLevel}`,
    undefined, opts,
  )

export const importInventoryCSV = (file: File) => {
  const fd = new FormData()
  fd.append('file', file)
  return request<{ imported: number; total_rows: number }>('POST', '/inventory/bulk', fd)
}

export const getInventoryDashboardSummary = (sessionId: string) =>
  request<InventoryDashboardSummary>('GET', `/inventory/dashboard-summary?session_id=${sessionId}`)

export const getStockHistory = (sku: string, days = 30) =>
  request<{ sku: string; days: number; history: { stock: number; date: string }[] }>(
    'GET', `/inventory/stock/${encodeURIComponent(sku)}/history?days=${days}`,
  )

// ── Shrinkage (non-sale stock-outs) ───────────────────────────────────────────
export const createShrinkage = (body: {
  sku: string; quantity: number; reason: ShrinkageReason
  warehouse?: string; notes?: string; occurred_at?: string
}) => request<ShrinkageRecord>('POST', '/inventory/shrinkage', body)

export const listShrinkage = (sku?: string, limit = 50) =>
  request<ShrinkageRecord[]>('GET', `/inventory/shrinkage?limit=${limit}${sku ? `&sku=${encodeURIComponent(sku)}` : ''}`)

export const listShrinkageReasons = () =>
  request<ShrinkageReason[]>('GET', '/inventory/shrinkage/reasons')

// ── Inventory events ──────────────────────────────────────────────────────────
export const listInventoryEvents   = () =>
  request<InventoryEvent[]>('GET', '/inventory/events')
export const getUpcomingEvents     = (days = 60) =>
  request<InventoryEvent[]>('GET', `/inventory/events/upcoming?days=${days}`)
export const createInventoryEvent  = (body: Omit<InventoryEvent, 'id' | 'tenant_id' | 'created_at'>) =>
  request<InventoryEvent>('POST', '/inventory/events', body)
export const updateInventoryEvent  = (id: string, body: Partial<InventoryEvent>) =>
  request<InventoryEvent>('PATCH', `/inventory/events/${id}`, body)

// ── Multiplicadores por product dentro de un event ────────────────────────
export const listEventMultipliers = (eventId: string) =>
  request<EventMultiplier[]>('GET', `/inventory/events/${eventId}/multipliers`)
export const setEventMultiplier = (
  eventId: string, scope: 'sku' | 'family' | 'category', scopeValue: string, multiplier: number,
) =>
  request<EventMultiplier>('PUT', `/inventory/events/${eventId}/multipliers`,
    { scope, scope_value: scopeValue, multiplier })
// Routed through `request` like its siblings so a failure arrives as an
// ApiError the i18n error layer can render, instead of a hardcoded sentence in
// one language.
export const deleteEventMultiplier = (eventId: string, overrideId: string) =>
  request<void>('DELETE', `/inventory/events/${eventId}/multipliers/${overrideId}`)

// ── LatAm commercial calendar (feature 3.4) ─────────────────────────────────
export const getCalendarCatalog = (country?: string) =>
  request<CalendarCatalogResponse>('GET', `/inventory/events/catalog${country ? `?country=${country}` : ''}`)
export const seedCalendarCatalog = (country?: string, years?: number[]) =>
  request<CalendarSeedResult>('POST', '/inventory/events/catalog/seed', { ...(country ? { country } : {}), years })
export const toggleCalendarEntry = (catalogKey: string, active: boolean) =>
  request<{ catalog_key: string; active: boolean; updated: number }>(
    'PATCH', `/inventory/events/catalog/${catalogKey}`, { active },
  )
export const deleteInventoryEvent  = (id: string) =>
  fetch(`${BASE}/inventory/events/${id}`, {
    method: 'DELETE',
    headers: { Authorization: `Bearer ${getToken()}` },
  }).then(() => undefined as void)

// ── Multi-warehouse (feature 5.4) ────────────────────────────────────────────
export const listWarehouses = () =>
  request<Warehouse[]>('GET', '/inventory/warehouses')
export const patchWarehouse = (name: string, demandShare: number | null) =>
  request<Warehouse>('PATCH', `/inventory/warehouses/${encodeURIComponent(name)}`,
    { demand_share: demandShare })
export const getStatusByWarehouse = (sessionId: string) =>
  request<WarehouseStatusResponse>(
    'GET', `/inventory/status?session_id=${sessionId}&by_warehouse=true`)
export const listTransfers = (status?: string) =>
  request<Transfer[]>('GET', `/inventory/transfers${status ? `?status=${status}` : ''}`)
export const createTransfer = (fromWarehouse: string, toWarehouse: string,
                               items: { sku: string; qty: number }[], notes?: string) =>
  request<Transfer>('POST', '/inventory/transfers',
    { from_warehouse: fromWarehouse, to_warehouse: toWarehouse, items, notes: notes ?? null })
export const receiveTransfer = (transferId: string,
                                lines: { sku: string; received_qty: number }[] | null) =>
  request<Transfer>('POST', `/inventory/transfers/${transferId}/receive`, { lines })
export const cancelTransfer = (transferId: string) =>
  request<Transfer>('POST', `/inventory/transfers/${transferId}/cancel`)
export const closeTransfer = (transferId: string) =>
  request<Transfer>('POST', `/inventory/transfers/${transferId}/close`)
export const createWarehouse = (name: string) =>
  request<Warehouse>('POST', '/inventory/warehouses', { name })

// Transfer lanes: how long a move between two warehouses takes and what it
// costs (PENDIENTES #2). Unconfigured pairs fall back to the backend default.
export const listTransferLanes = () =>
  request<TransferLane[]>('GET', '/inventory/warehouses/lanes')
export const upsertTransferLane = (lane: {
  from_warehouse: string; to_warehouse: string
  lead_time_days: number; cost_per_unit: number; fixed_cost: number
}) => request<TransferLane>('PUT', '/inventory/warehouses/lanes', lane)
export const deleteTransferLane = (fromWarehouse: string, toWarehouse: string) =>
  request<void>('DELETE', '/inventory/warehouses/lanes'
    + `?from_warehouse=${encodeURIComponent(fromWarehouse)}`
    + `&to_warehouse=${encodeURIComponent(toWarehouse)}`)

// ── PDF export ────────────────────────────────────────────────────────────────
export const downloadInventoryPDF = async (sessionId: string, serviceLevel = 0.95) => {
  const token = getToken()
  const res = await fetch(
    `${BASE}/inventory/report/pdf?session_id=${sessionId}&service_level=${serviceLevel}`,
    { headers: token ? { Authorization: `Bearer ${token}` } : {} },
  )
  if (!res.ok) throw new Error(`HTTP ${res.status}`)
  const blob = await res.blob()
  const url  = URL.createObjectURL(blob)
  const a    = document.createElement('a')
  a.href = url
  a.download = `inventory_${new Date().toISOString().slice(0, 10)}.pdf`
  a.click()
  URL.revokeObjectURL(url)
}

export const exportInventoryPO = async (sessionId: string, serviceLevel = 0.95) => {
  const token = getToken()
  const res = await fetch(
    `${BASE}/inventory/status/export-po?session_id=${sessionId}&service_level=${serviceLevel}`,
    { headers: token ? { Authorization: `Bearer ${token}` } : {} },
  )
  if (!res.ok) throw new Error(`HTTP ${res.status}`)
  const blob = await res.blob()
  const url  = URL.createObjectURL(blob)
  const a    = document.createElement('a')
  a.href = url; a.download = 'purchase_order.csv'; a.click()
  URL.revokeObjectURL(url)
  // After successful download, log the PO generation (fire and forget)
  logPOGeneration(sessionId).catch(() => {})
}

export const downloadInventoryTemplate = async () => {
  const token = getToken()
  const res = await fetch(`${BASE}/inventory/template.csv`, {
    headers: token ? { Authorization: `Bearer ${token}` } : {},
  })
  if (!res.ok) throw new Error(`HTTP ${res.status}`)
  const blob = await res.blob()
  const url  = URL.createObjectURL(blob)
  const a    = document.createElement('a')
  a.href = url; a.download = 'inventory_template.csv'; a.click()
  URL.revokeObjectURL(url)
}

// ── Inventory ROI ─────────────────────────────────────────────────────────────
export const getInventoryROI = () =>
  request<InventoryROISummary>('GET', '/inventory/roi')

export const getROIMonthly = (months = 6) =>
  request<import('./types').ROIMonthlyRow[]>('GET', `/inventory/roi/monthly?months=${months}`)

// Omitting year/month asks the backend for the month that just closed — the
// same period the monthly recap email covers.
export const getROIMonthReport = (year?: number, month?: number) =>
  request<import('./types').ROIMonthReport>(
    'GET',
    year && month
      ? `/inventory/roi/month-report?year=${year}&month=${month}`
      : '/inventory/roi/month-report',
  )

export const getPOHistory = (limit = 20, opts?: RequestOpts) =>
  request<POLogEntry[]>('GET', `/inventory/po-history?limit=${limit}`, undefined, opts)

// ── PO reception (cerrar el loop de purchase) ──────────────────────────────────
export const getPOItems = (poLogId: string) =>
  request<import('./types').POItemsResponse>('GET', `/inventory/po/${poLogId}/items`)

export const receivePO = (
  poLogId: string,
  body?: { lines?: { sku: string; received_qty: number }[]; received_at?: string },
) =>
  request<import('./types').ReceptionResult>('POST', `/inventory/po/${poLogId}/receive`, body ?? {})

export const sendPOToSuppliers = (poLogId: string) =>
  request<import('./types').SendPOResult>('POST', `/inventory/po/${poLogId}/send`)

export const getSupplierScorecard = () =>
  request<import('./types').SupplierScorecardRow[]>('GET', '/inventory/suppliers/scorecard')

export const getOverduePOs = () =>
  request<import('./types').OverdueReception[]>('GET', '/inventory/po/overdue')

export const getSupplierContactHealth = () =>
  request<import('./types').SupplierContactHealthRow[]>('GET', '/inventory/suppliers/contact-health')

export const getSupplierLeadTimeAlerts = () =>
  request<import('./types').SupplierLeadTimeAlert[]>('GET', '/inventory/suppliers/lead-time-alerts')

// ── Supplier price breaks (feature 3.5) ──────────────────────────────────────
export const getPriceBreaks = (params?: { supplier_id?: string; sku?: string }) => {
  const qs = new URLSearchParams()
  if (params?.supplier_id) qs.set('supplier_id', params.supplier_id)
  if (params?.sku) qs.set('sku', params.sku)
  const suffix = qs.toString() ? `?${qs.toString()}` : ''
  return request<import('./types').PriceBreak[]>('GET', `/inventory/price-breaks${suffix}`)
}

export const upsertPriceBreak = (
  supplierId: string,
  body: { sku: string; min_qty: number; unit_price: number; notes?: string },
) =>
  request<import('./types').PriceBreak>(
    'POST', `/inventory/suppliers/${supplierId}/price-breaks`, body,
  )

export const deletePriceBreak = (priceBreakId: string) =>
  request<void>('DELETE', `/inventory/price-breaks/${priceBreakId}`)

// Evaluated against the cart the browser holds, so the quantities the buyer
// actually edited are what gets judged.
export const evaluatePriceBreaks = (
  sessionId: string,
  items: { sku: string; quantity: number }[],
) =>
  request<import('./types').PriceBreakEvaluation>(
    'POST', `/inventory/price-breaks/evaluate?session_id=${sessionId}`, { items },
  )

// ── Cash calendar (feature 3.6) ──────────────────────────────────────────────
export const getCashCalendar = (horizonDays = 30) =>
  request<import('./types').CashCalendar>(
    'GET', `/inventory/cash-calendar?horizon_days=${horizonDays}`,
  )

export const checkCashFit = (
  body: {
    items: { sku?: string | null; supplier_name?: string | null; quantity: number; unit_cost?: number | null }[]
    budget?: number
  },
  horizonDays = 30,
) =>
  request<import('./types').CashFitResult>(
    'POST', `/inventory/cash-calendar/fit?horizon_days=${horizonDays}`, body,
  )

// ── Event / promo impact simulator ───────────────────────────────────────────
export const simulateEvent = (body: {
  session_id: string
  event_id?: string
  start_date?: string
  end_date?: string
  multiplier?: number
  name?: string
}) =>
  request<import('./types').EventSimulationResult>('POST', '/inventory/events/simulate', body)

export const logPOGeneration = (
  sessionId: string,
  items?: POLineDecision[],
  destinationWarehouse?: string,
) => {
  // destination_warehouse omitted = tenant default warehouse (mono-warehouse
  // tenants never send it, so their behavior is byte-identical to before 5.4).
  const body: { items?: POLineDecision[]; destination_warehouse?: string } = {}
  if (items && items.length) body.items = items
  if (destinationWarehouse) body.destination_warehouse = destinationWarehouse
  return request<POLogEntry>(
    'POST',
    `/inventory/log-po?session_id=${sessionId}`,
    Object.keys(body).length ? body : undefined,
  )
}

export const getMorningBriefing = (sessionId: string, serviceLevel = 0.95, opts?: RequestOpts) =>
  request<MorningBriefing>(
    'GET',
    `/inventory/morning-briefing?session_id=${sessionId}&service_level=${serviceLevel}`,
    undefined, opts,
  )

export const setUserPermissions = (id: string, permissions: string[]) =>
  request<{ user_id: string; permissions: string[] }>('PATCH', `/users/${id}/permissions`, { permissions })

export const resendVerification = (id: string) =>
  request<{ message: string; email: string }>('POST', `/users/${id}/resend-verification`)

// ── User Preferences ──────────────────────────────────────────────────────────
export const getPreferences = () =>
  request<import('./types').UserPreferences>('GET', '/me/preferences')

export const updatePreferences = (body: Partial<import('./types').UserPreferences>) =>
  request<import('./types').UserPreferences>('PATCH', '/me/preferences', body)

// ── Team messaging (direct messages between users of the tenant) ──────────────
export const getDmContacts = () =>
  request<import('./types').DmContact[]>('GET', '/messages/contacts')

export const getDmConversations = () =>
  request<import('./types').DmConversation[]>('GET', '/messages/conversations')

export const getDmUnreadCount = () =>
  request<{ unread: number }>('GET', '/messages/unread-count', undefined, { silent: true })

export const getDmThread = (withUser: string, before?: number) =>
  request<import('./types').DmThread>(
    'GET', `/messages/thread?with_user=${encodeURIComponent(withUser)}${before ? `&before=${before}` : ''}`,
  )

export const sendDm = (recipientId: string, body: string) =>
  request<import('./types').DirectMessage>('POST', '/messages', { recipient_id: recipientId, body })

export const markDmRead = (withUser: string) =>
  request<{ read: boolean }>('POST', '/messages/read', { with_user: withUser })

// ── Activity Logs ─────────────────────────────────────────────────────────────
export const getActivityLogs = (params?: { limit?: number; offset?: number; action?: string }) => {
  const q = new URLSearchParams()
  if (params?.limit  !== undefined) q.set('limit',  String(params.limit))
  if (params?.offset !== undefined) q.set('offset', String(params.offset))
  if (params?.action)               q.set('action', params.action)
  const qs = q.toString()
  return request<import('./types').ActivityLogsResponse>('GET', `/me/activity${qs ? `?${qs}` : ''}`)
}

export const getActivityActionTypes = () =>
  request<string[]>('GET', '/me/activity/action-types')

// ── Platform Models ───────────────────────────────────────────────────────────
export const getPlatformModels = () =>
  request<import('./types').PlatformModel[]>('GET', '/models')

// ── Statistical Analysis ──────────────────────────────────────────────────────
// ── SKU Intelligence ──────────────────────────────────────────────────────────
export const getSkuIntelligence = (
  sessionId: string,
  sku: string,
  params?: { model?: string; granularity?: string; agg?: string },
  opts?: RequestOpts,
) => {
  const q = new URLSearchParams()
  if (params?.model)       q.set('model',       params.model)
  if (params?.granularity) q.set('granularity', params.granularity)
  if (params?.agg)         q.set('agg',         params.agg)
  const qs = q.toString()
  return request<import('./types').SkuIntelligenceData>(
    'GET',
    `/sessions/${sessionId}/sku-intelligence/${encodeURIComponent(sku)}${qs ? `?${qs}` : ''}`,
    undefined, opts,
  )
}

/** STL split of one SKU's history into trend / seasonal / residual.
 *
 *  Refuses rather than degrades: too little history comes back as a 422 with
 *  `decomposition_history_too_short` and the numbers needed to explain it. */
export const getSkuDecomposition = (
  sessionId: string,
  sku: string,
  params?: { granularity?: string },
  opts?: RequestOpts,
) => {
  const q = new URLSearchParams()
  if (params?.granularity) q.set('granularity', params.granularity)
  const qs = q.toString()
  return request<import('./types').DecompositionData>(
    'GET',
    `/sessions/${sessionId}/decomposition/${encodeURIComponent(sku)}${qs ? `?${qs}` : ''}`,
    undefined, opts,
  )
}

// ── Currency (the customer's own money, not what Faro costs) ─────────────────
export const getTenantCurrency = (opts?: RequestOpts) =>
  request<{
    current: import('./currency').CurrencyInfo
    supported: import('./currency').CurrencyInfo[]
  }>('GET', '/tenant/currency', undefined, opts)

/** Admin only. Relabels existing figures; it does not convert them. */
export const setTenantCurrency = (code: string) =>
  request<{ current: import('./currency').CurrencyInfo }>(
    'PATCH', '/tenant/currency', { code })

// ── Billing ───────────────────────────────────────────────────────────────────
export const getSubscription = (opts?: RequestOpts) =>
  request<import('./types').SubscriptionState>('GET', '/billing/subscription',
    undefined, opts)

/** Starts a Stripe-hosted checkout and returns the URL to send the browser to.
 *
 *  Note it takes a PLAN, not a price id: the server resolves the price from its
 *  own configuration, so a client cannot check out against a price of its
 *  choosing. Admin only. */
export const startCheckout = (plan: string, interval: 'monthly' | 'yearly' = 'monthly') =>
  request<{ url: string }>('POST', '/billing/checkout', { plan, interval })

/** Stripe's own billing portal: cards, invoices, cancellation. Admin only. */
export const openBillingPortal = () =>
  request<{ url: string }>('POST', '/billing/portal', {})

export const analyzeDataSource = (
  id: string,
  params: { date_col: string; target_col: string; sku_col?: string; sheet?: string; date_from?: string; date_to?: string },
) => {
  const q = new URLSearchParams({ date_col: params.date_col, target_col: params.target_col })
  if (params.sku_col)   q.set('sku_col',   params.sku_col)
  if (params.sheet)     q.set('sheet',     params.sheet)
  if (params.date_from) q.set('date_from', params.date_from)
  if (params.date_to)   q.set('date_to',   params.date_to)
  return request<import('./types').AnalysisResult>('GET', `/data-sources/${id}/analyze?${q}`)
}

export const analyzeSkuDetail = (
  id: string,
  sku: string,
  params: { date_col: string; target_col: string; sku_col?: string; sheet?: string; date_from?: string; date_to?: string },
) => {
  const q = new URLSearchParams({ date_col: params.date_col, target_col: params.target_col })
  if (params.sku_col)   q.set('sku_col',   params.sku_col)
  if (params.sheet)     q.set('sheet',     params.sheet)
  if (params.date_from) q.set('date_from', params.date_from)
  if (params.date_to)   q.set('date_to',   params.date_to)
  return request<import('./types').SkuDetailResult>(
    'GET', `/data-sources/${id}/analyze/${encodeURIComponent(sku)}?${q}`,
  )
}

// ── Documents ─────────────────────────────────────────────────────────────────
export interface DocumentMeta {
  id: string
  name: string
  original_name: string
  file_type: string
  file_size: number
  status: 'PENDING' | 'INDEXING' | 'INDEXED' | 'FAILED'
  chunk_count: number | null
  page_count: number | null
  error: string | null
  uploaded_by: string | null
  uploaded_at: string | null
  indexed_at: string | null
}

export const listDocuments = () =>
  request<DocumentMeta[]>('GET', '/documents')

export const uploadDocument = (fd: FormData) =>
  request<DocumentMeta>('POST', '/documents', fd)

export const getDocumentStatus = (docId: string) =>
  request<{ doc_id: string; status: string; chunk_count: number | null; page_count: number | null; error: string | null }>(
    'GET', `/documents/${docId}/status`,
  )

export const deleteDocument = (docId: string) =>
  request<{ deleted: string }>('DELETE', `/documents/${docId}`)

export function getDocumentContentUrl(docId: string): string {
  const base = (process.env.NEXT_PUBLIC_API_URL ?? 'http://localhost:8000').replace(/\/$/, '')
  return `${base}/api/v1/documents/${docId}/content`
}

// ── Suppliers ─────────────────────────────────────────────────────────────────
export const listSuppliers    = (opts?: RequestOpts) =>
  request<Supplier[]>('GET', '/inventory/suppliers', undefined, opts)

export const createSupplier   = (body: Omit<Supplier, 'id' | 'tenant_id' | 'created_at' | 'active'>) =>
  request<Supplier>('POST', '/inventory/suppliers', body)

export const updateSupplier   = (id: string, body: Partial<Supplier>) =>
  request<Supplier>('PATCH', `/inventory/suppliers/${id}`, body)

export const deleteSupplier   = (id: string) =>
  fetch(`${BASE}/inventory/suppliers/${id}`, {
    method: 'DELETE',
    headers: { Authorization: `Bearer ${getToken()}` },
  }).then(() => undefined as void)

/** Manual purchase order — supplier chosen explicitly, no forecast session. */
/** Deliver a PO to the buyer's own WhatsApp; always returns text + wa.me link. */
export const sendPOToSelf = (poLogId: string) =>
  request<{ sent: boolean; has_number: boolean; message_text: string; wa_me_url: string }>(
    'POST', `/inventory/po/${poLogId}/send-to-me`,
  )

export const createManualPO = (body: {
  supplier_id: string
  lines: { sku: string; qty: number; unit_cost?: number; display_name?: string }[]
  destination_warehouse?: string
}) =>
  request<POLogEntry>('POST', '/inventory/po', body)

export const getSkuSuppliers  = (sku: string) =>
  request<SkuSupplier[]>('GET', `/inventory/stock/${encodeURIComponent(sku)}/suppliers`)

export const assignSkuSupplier = (sku: string, supplierId: string, body: {
  is_primary?: boolean; unit_cost?: number; moq?: number; lead_time_days?: number
}) =>
  request<SkuSupplier>('PUT', `/inventory/stock/${encodeURIComponent(sku)}/suppliers/${supplierId}`, body)

export const removeSkuSupplier = (sku: string, supplierId: string) =>
  fetch(`${BASE}/inventory/stock/${encodeURIComponent(sku)}/suppliers/${supplierId}`, {
    method: 'DELETE',
    headers: { Authorization: `Bearer ${getToken()}` },
  }).then(() => undefined as void)

// ── Dead stock / immobilised inventory ────────────────────────────────────────
export const getDeadStock = (sessionId: string, minDays = 30) =>
  request<DeadStockResponse>('GET', `/inventory/dead-stock?session_id=${sessionId}&min_days_static=${minDays}`)

// Default 30 matches the backend's default — see the endpoint's comment on
// why a shorter horizon locks out any SKU whose lead time isn't configured.
export const optimizeInventory = (sessionId: string, horizonDays = 30) =>
  request<OptimizationResponse>(
    'GET', `/inventory/optimize?session_id=${sessionId}&horizon_days=${horizonDays}`,
  )

// ── AI Narrative Intelligence ─────────────────────────────────────────────────
// `silent: true` — the caller already renders a rules-based summary when this
// fails or takes too long, and it says so on screen ("Análisis basado en
// reglas"). The global toast has no way to know that, so it was raising "Algo
// falló de nuestro lado" over a panel that had already recovered — and, because
// the request outlived the navigation, sometimes on a completely different
// page. A degraded AI summary is not an error the buyer needs to act on.
export const getMorningNarrative = (sessionId: string, profile = 'distributor') =>
  request<import('./types').MorningNarrative>(
    'POST', '/ai/narrative/morning', { session_id: sessionId, profile },
    { silent: true },
  )


export const getSuggestedQuestions = (profile = 'distributor', hasInventory = true, hasProduction = false) =>
  request<import('./types').SuggestedQuestion[]>(
    'POST', '/ai/suggested-questions', { profile, has_inventory: hasInventory, has_production: hasProduction }
  )

// ── Entitlements ──────────────────────────────────────────────────────────────
export interface Entitlements {
  plan: string
  trial: { state: string; ends_at: string | null }
  limits: Record<string, number | null>
  features: Record<string, boolean>
  // Minimum plan that unlocks each feature (e.g. { ai_analyst: 'professional' }).
  feature_plans: Record<string, string>
  read_only: boolean
}

export const getEntitlements = () =>
  request<Entitlements>('GET', '/entitlements')

// ── Accounting integrations ────────────────────────────────────────────────────
export interface Integration {
  id:            string
  provider:      string
  status:        string
  last_sync_at:  string | null
  last_error:    string | null
  created_at:    string
}

export interface ProviderInfo {
  fields: string[]
}

export interface IntegrationsListResponse {
  connections: Integration[]
  providers:   Record<string, ProviderInfo>
}

export const listIntegrations = (opts?: RequestOpts) =>
  request<IntegrationsListResponse>('GET', '/integrations', undefined, opts)

export const connectIntegration = (provider: string, creds: Record<string, string>) =>
  request<Integration>('POST', `/integrations/${encodeURIComponent(provider)}/connect`, creds)

export const syncIntegration = (id: string) =>
  request<unknown>('POST', `/integrations/${encodeURIComponent(id)}/sync`)

export const deleteIntegration = (id: string) =>
  request<{ deleted: string }>('DELETE', `/integrations/${encodeURIComponent(id)}`)

// ── Multi-period planning (Phase B) ──────────────────────────────────────────
export const getPlanning = () =>
  request<PlanningState>('GET', '/planning')
export const setPlanning = (period: PlanningPeriod, horizon: number) =>
  request<PlanningState>('PUT', '/planning', { period, horizon })

// ── What-if scenarios (PENDIENTES #7) ────────────────────────────────────────
// `previewScenario` runs inline rules without saving them (the builder's live
// comparison); `runScenario` replays a saved one. Both are pure reads.
export const listScenarios = (sessionId: string, opts?: RequestOpts) =>
  request<import('./types').Scenario[]>(
    'GET', `/sessions/${encodeURIComponent(sessionId)}/scenarios`, undefined, opts,
  )

export const createScenario = (
  sessionId: string,
  name: string,
  rules: import('./types').ScenarioRule[],
) =>
  request<import('./types').Scenario>(
    'POST', `/sessions/${encodeURIComponent(sessionId)}/scenarios`, { name, rules },
  )

export const deleteScenario = (scenarioId: string) =>
  request<{ deleted: string }>('DELETE', `/scenarios/${encodeURIComponent(scenarioId)}`)

export const previewScenario = (
  sessionId: string,
  rules: import('./types').ScenarioRule[],
  opts?: RequestOpts,
) =>
  request<import('./types').ScenarioRunResult>(
    'POST', `/sessions/${encodeURIComponent(sessionId)}/scenarios/preview`, { rules }, opts,
  )

export const runScenario = (sessionId: string, scenarioId: string, opts?: RequestOpts) =>
  request<import('./types').ScenarioRunResult>(
    'POST',
    `/sessions/${encodeURIComponent(sessionId)}/scenarios/${encodeURIComponent(scenarioId)}/run`,
    undefined, opts,
  )

// ── Data freshness ────────────────────────────────────────────────────────────
// How old the two inputs behind the semáforo are. Sales and stock age on
// separate clocks (stock in days, sales in weeks), and `semaphore` is the
// verdict the traffic light has to obey: past the blind thresholds it may no
// longer claim a colour. Same computation the daily reminder email runs, so the
// screen and the email can never disagree.

export interface FreshnessClock {
  age_days:   number | null
  /** 'unknown' (never loaded) | 'fresh' | 'stale' (warn) | 'blind' (degrade) */
  state:      'unknown' | 'fresh' | 'stale' | 'blind'
  stale_days: number
  blind_days: number
}

export interface SalesFreshness extends FreshnessClock {
  session_id:   string | null
  session_name: string | null
  trained_at:   string | null
  /** Last date INSIDE the file (YYYY-MM-DD), when the profiler could read it. */
  data_through: string | null
  /** 'data_date' when age comes from the file's last date, 'upload_date' otherwise. */
  basis:        'data_date' | 'upload_date' | null
  reminder_days: number
}

export interface StockFreshness extends FreshnessClock {
  tracked_skus: number
  updated_at:   string | null
}

export interface DataFreshnessInfo {
  sales:       SalesFreshness
  stock:       StockFreshness
  semaphore:   'current' | 'degraded'
  degraded_by: Array<'stock' | 'sales'>
  warn:        boolean
}

export const getDataFreshness = (opts?: RequestOpts) =>
  request<DataFreshnessInfo>('GET', '/data-freshness', undefined, opts)

// ── Stock setup: Pareto gaps + tolerant importer (friction plan #1, 3-4) ─────
// `getSetupGaps` ranks the unconfigured SKUs by the MONEY they move, so the UI
// can ask for 40 rows instead of 2.000. `previewStockImport` is the dry run
// behind the column-mapping wizard; `importStockFile` commits it and accepts
// .xlsx as well as CSV.
export const getSetupGaps = (
  params: { sessionId?: string; horizonDays?: number; limit?: number; targetPct?: number } = {},
  opts?: RequestOpts,
) => {
  const q = new URLSearchParams()
  if (params.sessionId)   q.set('session_id', params.sessionId)
  if (params.horizonDays) q.set('horizon_days', String(params.horizonDays))
  if (params.limit)       q.set('limit', String(params.limit))
  if (params.targetPct)   q.set('target_pct', String(params.targetPct))
  const qs = q.toString()
  return request<import('./stockSetupTypes').SetupGapsResponse>(
    'GET', `/inventory/setup-gaps${qs ? `?${qs}` : ''}`, undefined, opts,
  )
}

export const previewStockImport = (
  file: File,
  mapping?: import('./stockSetupTypes').StockImportMapping,
  opts?: RequestOpts,
) => {
  const fd = new FormData()
  fd.append('file', file)
  if (mapping) fd.append('mapping', JSON.stringify(mapping))
  return request<import('./stockSetupTypes').StockImportPreview>(
    'POST', '/inventory/bulk/preview', fd, opts,
  )
}

export const importStockFile = (
  file: File,
  mapping?: import('./stockSetupTypes').StockImportMapping,
  opts?: RequestOpts,
) => {
  const fd = new FormData()
  fd.append('file', file)
  if (mapping) fd.append('mapping', JSON.stringify(mapping))
  return request<import('./stockSetupTypes').StockImportResult>(
    'POST', '/inventory/bulk', fd, opts,
  )
}

// ── Alert history (the bell) ─────────────────────────────────────────────────
// The 08:00 UTC loop's stockout digests, supplier lead-time warnings and
// data-freshness reminders leave the building by email/WhatsApp and, until
// this endpoint, left no trace inside the app: delete the mail and the
// information was gone. `GET /alerts` reads back the delivery rows the loop
// already writes to activity_logs — including the ones that FAILED.

export const getAlertHistory = (limit = 20, opts?: RequestOpts) =>
  request<import('../components/alerts/types').AlertHistory>(
    'GET', `/alerts?limit=${limit}`, undefined, opts,
  )

/** Marks every alert up to now as read for the calling user. Mutating, so it
 *  takes the analyst+ guard — a viewer is never an alert recipient. */
export const markAlertsRead = (opts?: RequestOpts) =>
  request<import('../components/alerts/types').MarkAlertsReadResult>(
    'POST', '/alerts/read', undefined, opts,
  )
