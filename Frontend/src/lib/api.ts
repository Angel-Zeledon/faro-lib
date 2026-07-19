import type {
  SessionInfo, DatasetMeta, DataProfile, ColumnOptions, InspectionResult,
  QualityReport, ConfigSchema, ChooseColumnsBody, CanonicalColumnsBody,
  JobResponse, MetricsResponse, InventoryResponse, RoutingPlan,
  ForecastSeries, DataHealthReport,
  Chat, ChatMessage, MessagesPage, ChatSourceType,
  DataSource, DataPreview, SqlQueryResult, SqlEngine,
  InventoryStock, InventoryStatusResponse, InventoryDashboardSummary,
  InventoryEvent, InventoryROISummary, POLogEntry, POLineDecision,
  Supplier, SkuSupplier, MorningBriefing, DeadStockResponse, OptimizationResponse,
  MermaReason, MermaRecord,
} from './types'
import { getToken, clearAuth, tryRefresh } from './auth'

const BASE = '/api'

let _redirectingToLogin = false

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

async function request<T = unknown>(method: string, path: string, body?: unknown): Promise<T> {
  let res = await _doFetch(method, path, body)

  if (res.status === 401) {
    // Auth endpoints return 401 for wrong credentials/tokens — surface that
    // error to the form instead of treating it as an expired session.
    if (path.startsWith('/auth/')) {
      const err = await res.json().catch(() => ({ detail: res.statusText }))
      throw new Error(extractErrorMessage(err) || 'Credenciales inválidas')
    }
    // Expired access token: renew silently with the refresh token and retry
    // once, so a 15-minute token never kicks the user back to /login mid-task.
    if (await tryRefresh()) {
      res = await _doFetch(method, path, body)
      if (res.status === 401) _sessionLost()
    } else {
      _sessionLost()
    }
  }

  if (!res.ok) {
    const err = await res.json().catch(() => ({ detail: res.statusText }))
    throw new Error(extractErrorMessage(err) || `HTTP ${res.status}`)
  }

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
    const err = await res.json().catch(() => ({ detail: res.statusText }))
    throw new Error((err as { detail?: string }).detail || `HTTP ${res.status}`)
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
}) =>
  request<{ user: Record<string, unknown>; tenant: Record<string, unknown> }>(
    'POST', '/auth/signup', body,
  )

export const authLogin = (email: string, password: string) =>
  request<{
    access_token:  string
    refresh_token: string
    token_type:    string
    expires_in:    number
    user: { id: string; email: string; full_name: string | null; role: string; tenant_id: string }
  }>('POST', '/auth/login', { email, password })

export const authVerifyEmail = (token: string) =>
  request<{ message: string }>('POST', '/auth/verify-email', { token })

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

export const attachDataset = (sessionId: string, dataset_id: string) =>
  request<SessionInfo>('POST', `/sessions/${sessionId}/dataset`, { dataset_id })

export const inspectSession = (id: string) =>
  request<InspectionResult>('GET', `/sessions/${id}/inspect`)

export const getQuality     = (id: string) => request<QualityReport>('GET', `/sessions/${id}/quality`)
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
export const getSavedValidation   = (id: string) => request<Record<string, unknown>>('GET', `/sessions/${id}/configure/validation`)

export const getAvailableModels = (id: string) =>
  request<{ models: string[] }>('GET', `/sessions/${id}/available-models`)

export const getDatasetAnalysis = (id: string) =>
  request<import('./types').DatasetAnalysis>('GET', `/sessions/${id}/analysis`)

export const getModelHyperparams = (id: string) =>
  request<Record<string, import('./types').HyperparamDef[]>>('GET', `/sessions/${id}/models/hyperparams`)

export const getConfigSummary = (id: string) =>
  request<Record<string, unknown>>('GET', `/sessions/${id}/config-summary`)

// ── Training ──────────────────────────────────────────────────────────────────
export const startTraining = (id: string) =>
  request<{ job_id: string; status: string }>('POST', `/sessions/${id}/train`)

// One-click demo: seeds dataset + configs + stock and queues training
export const startDemoQuickstart = () =>
  request<{ session_id: string; job_id: string; dataset_id: string; stock_seeded: string[] }>(
    'POST', '/demo/quickstart',
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

export const getResults = (id: string) =>
  request<Record<string, unknown>>('GET', `/sessions/${id}/results`)

export const getRoutingPlan = (id: string) =>
  request<RoutingPlan>('GET', `/sessions/${id}/routing`)

export const exportConfig = (id: string) =>
  request<Record<string, unknown>>('GET', `/sessions/${id}/config-summary`)
// ── Forecast Series (ECharts) ─────────────────────────────────────────────────
export const getForecastSeries = (sessionId: string, sku: string, model?: string) =>
  request<ForecastSeries>(
    'GET',
    `/sessions/${sessionId}/forecast-series/${encodeURIComponent(sku)}${model ? `?model=${model}` : ''}`,
  )

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

export const narrateData = (
  sessionId: string,
  data: unknown,
  context: string,
  question?: string,
  history?: { role: 'user' | 'assistant'; content: string }[],
) =>
  request<{ narrative: string; source: string; tokens_used: number | null; question: string | null }>(
    'POST', `/sessions/${sessionId}/analyst/narrate`, { data, context, question, history },
  )

// ── Reports ───────────────────────────────────────────────────────────────────
export const generateReport = (sessionId: string, type = 'operational', formats = ['excel', 'pdf']) =>
  request<{ message: string; type: string; formats: string[] }>(
    'POST', `/sessions/${sessionId}/reports/generate`, { type, formats },
  )

export const downloadReportBlob = (sessionId: string, format: 'excel' | 'pdf') =>
  downloadBlob(
    `/sessions/${sessionId}/reports/${format}`,
    `report_${sessionId.slice(0, 8)}.${format === 'excel' ? 'xlsx' : 'pdf'}`,
  )

export const getRagStatus = (sessionId: string) =>
  request<{ indexed: boolean; vector_count: number }>(
    'GET', `/sessions/${sessionId}/analyst/rag-status`,
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

export const getDataSourcePreview = (id: string, rows = 100, sheet?: string) =>
  request<DataPreview>('GET', `/data-sources/${id}/preview?rows=${rows}${sheet ? `&sheet=${encodeURIComponent(sheet)}` : ''}`)

export const renameDataSource = (id: string, name: string, description?: string) =>
  request<DataSource>('PATCH', `/data-sources/${id}`, { name, description })

export const deleteDataSource = (id: string) =>
  request<{ deleted: string }>('DELETE', `/data-sources/${id}`)

// ── Drift Monitoring ──────────────────────────────────────────────────────────
export interface DriftReport {
  session_id:    string
  target_col:    string
  feature_drift: Record<string, { psi: number; psi_level: string; ks_p_value: number; drift: boolean }>
  alerts:        string[]
  has_drift:     boolean
  ref_rows:      number
  cur_rows:      number
}
export const detectDrift = (sessionId: string, fd: FormData) =>
  request<DriftReport>('POST', `/sessions/${sessionId}/drift`, fd)

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
  request<Record<string, unknown>>('GET', '/users/me')

export const updateMe = (body: { full_name?: string; whatsapp_number?: string }) =>
  request<Record<string, unknown>>('PATCH', '/users/me', body)

export const requestPasswordChange = (new_password: string) =>
  request<{ message: string }>('POST', '/users/me/change-password/request', { new_password })

export const confirmPasswordChange = (code: string, new_password: string) =>
  request<{ message: string }>('POST', '/users/me/change-password/confirm', { code, new_password })

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

// ── Forecast Overrides ────────────────────────────────────────────────────────
export const saveForecastOverrides = (
  sessionId: string,
  overrides: import('./types').ForecastOverride[],
) =>
  request<{ saved: number }>('PATCH', `/sessions/${sessionId}/overrides`, overrides)

export const getForecastOverrides = (sessionId: string) =>
  request<import('./types').ForecastOverride[]>('GET', `/sessions/${sessionId}/overrides`)

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
export const getSchedule = (sessionId: string) =>
  request<import('./types').JobSchedule>('GET', `/sessions/${sessionId}/schedule`)
    .catch((e: Error) => { if (e.message.includes('404')) return null; throw e })

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

export const getInventoryStatus = (sessionId: string, serviceLevel = 0.95) =>
  request<InventoryStatusResponse>(
    'GET',
    `/inventory/status?session_id=${sessionId}&service_level=${serviceLevel}`,
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

// ── Mermas (shrinkage / non-sale stock-outs) ──────────────────────────────────
export const createMerma = (body: {
  sku: string; quantity: number; reason: MermaReason
  bodega?: string; notes?: string; occurred_at?: string
}) => request<MermaRecord>('POST', '/inventory/mermas', body)

export const listMermas = (sku?: string, limit = 50) =>
  request<MermaRecord[]>('GET', `/inventory/mermas?limit=${limit}${sku ? `&sku=${encodeURIComponent(sku)}` : ''}`)

export const listMermaReasons = () =>
  request<MermaReason[]>('GET', '/inventory/mermas/reasons')

// ── Inventory events ──────────────────────────────────────────────────────────
export const listInventoryEvents   = () =>
  request<InventoryEvent[]>('GET', '/inventory/events')
export const getUpcomingEvents     = (days = 60) =>
  request<InventoryEvent[]>('GET', `/inventory/events/upcoming?days=${days}`)
export const createInventoryEvent  = (body: Omit<InventoryEvent, 'id' | 'tenant_id' | 'created_at'>) =>
  request<InventoryEvent>('POST', '/inventory/events', body)
export const updateInventoryEvent  = (id: string, body: Partial<InventoryEvent>) =>
  request<InventoryEvent>('PATCH', `/inventory/events/${id}`, body)
export const deleteInventoryEvent  = (id: string) =>
  fetch(`${BASE}/inventory/events/${id}`, {
    method: 'DELETE',
    headers: { Authorization: `Bearer ${getToken()}` },
  }).then(() => undefined as void)

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
  a.download = `inventario_${new Date().toISOString().slice(0, 10)}.pdf`
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
  a.href = url; a.download = 'orden_de_compra.csv'; a.click()
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
  a.href = url; a.download = 'plantilla_inventario.csv'; a.click()
  URL.revokeObjectURL(url)
}

// ── Inventory ROI ─────────────────────────────────────────────────────────────
export const getInventoryROI = () =>
  request<InventoryROISummary>('GET', '/inventory/roi')

export const getROIMonthly = (months = 6) =>
  request<import('./types').ROIMonthlyRow[]>('GET', `/inventory/roi/monthly?months=${months}`)

export const getPOHistory = (limit = 20) =>
  request<POLogEntry[]>('GET', `/inventory/po-history?limit=${limit}`)

// ── PO reception (cerrar el loop de compra) ──────────────────────────────────
export const getPOItems = (poLogId: string) =>
  request<import('./types').POItemsResponse>('GET', `/inventory/po/${poLogId}/items`)

export const receivePO = (
  poLogId: string,
  body?: { lines?: { sku: string; cantidad_recibida: number }[]; received_at?: string },
) =>
  request<import('./types').ReceptionResult>('POST', `/inventory/po/${poLogId}/receive`, body ?? {})

export const sendPOToSuppliers = (poLogId: string) =>
  request<import('./types').SendPOResult>('POST', `/inventory/po/${poLogId}/send`)

export const getSupplierScorecard = () =>
  request<import('./types').SupplierScorecardRow[]>('GET', '/inventory/suppliers/scorecard')

export const getOverduePOs = () =>
  request<import('./types').OverdueReception[]>('GET', '/inventory/po/overdue')

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

export const logPOGeneration = (sessionId: string, items?: POLineDecision[]) =>
  request<POLogEntry>(
    'POST',
    `/inventory/log-po?session_id=${sessionId}`,
    items && items.length ? { items } : undefined,
  )

export const getMorningBriefing = (sessionId: string, serviceLevel = 0.95) =>
  request<MorningBriefing>(
    'GET',
    `/inventory/morning-briefing?session_id=${sessionId}&service_level=${serviceLevel}`,
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
) => {
  const q = new URLSearchParams()
  if (params?.model)       q.set('model',       params.model)
  if (params?.granularity) q.set('granularity', params.granularity)
  if (params?.agg)         q.set('agg',         params.agg)
  const qs = q.toString()
  return request<import('./types').SkuIntelligenceData>(
    'GET',
    `/sessions/${sessionId}/sku-intelligence/${encodeURIComponent(sku)}${qs ? `?${qs}` : ''}`,
  )
}

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
export const listSuppliers    = () =>
  request<Supplier[]>('GET', '/inventory/suppliers')

export const createSupplier   = (body: Omit<Supplier, 'id' | 'tenant_id' | 'created_at' | 'active'>) =>
  request<Supplier>('POST', '/inventory/suppliers', body)

export const updateSupplier   = (id: string, body: Partial<Supplier>) =>
  request<Supplier>('PATCH', `/inventory/suppliers/${id}`, body)

export const deleteSupplier   = (id: string) =>
  fetch(`${BASE}/inventory/suppliers/${id}`, {
    method: 'DELETE',
    headers: { Authorization: `Bearer ${getToken()}` },
  }).then(() => undefined as void)

export const getSkuSuppliers  = (sku: string) =>
  request<SkuSupplier[]>('GET', `/inventory/stock/${encodeURIComponent(sku)}/suppliers`)

export const assignSkuSupplier = (sku: string, supplierId: string, body: {
  is_primary?: boolean; unit_cost?: number; moq?: number; lead_time_dias?: number
}) =>
  request<SkuSupplier>('PUT', `/inventory/stock/${encodeURIComponent(sku)}/suppliers/${supplierId}`, body)

export const removeSkuSupplier = (sku: string, supplierId: string) =>
  fetch(`${BASE}/inventory/stock/${encodeURIComponent(sku)}/suppliers/${supplierId}`, {
    method: 'DELETE',
    headers: { Authorization: `Bearer ${getToken()}` },
  }).then(() => undefined as void)

// ── Dead Stock / Inventario Inmovilizado ──────────────────────────────────────
export const getDeadStock = (sessionId: string, minDays = 30) =>
  request<DeadStockResponse>('GET', `/inventory/dead-stock?session_id=${sessionId}&min_days_static=${minDays}`)

// Default 30 matches the backend's default — see the endpoint's comment on
// why a shorter horizon locks out any SKU whose lead time isn't configured.
export const optimizeInventory = (sessionId: string, horizonDays = 30) =>
  request<OptimizationResponse>(
    'GET', `/inventory/optimize?session_id=${sessionId}&horizon_days=${horizonDays}`,
  )

// ── AI Narrative Intelligence ─────────────────────────────────────────────────
export const getMorningNarrative = (sessionId: string, profile = 'distributor') =>
  request<import('./types').MorningNarrative>(
    'POST', '/ai/narrative/morning', { session_id: sessionId, profile }
  )

export const getInventoryInsight = (sessionId: string, profile = 'distributor') =>
  request<import('./types').InventoryInsight>(
    'POST', '/ai/narrative/inventory', { session_id: sessionId, profile }
  )

export const getForecastExplanation = (sku: string, sessionId: string, profile = 'distributor') =>
  request<import('./types').ForecastExplanation>(
    'POST', '/ai/narrative/forecast-explanation', { sku, session_id: sessionId, profile }
  )

export const getSuggestedQuestions = (profile = 'distributor', hasInventory = true, hasProduction = false) =>
  request<import('./types').SuggestedQuestion[]>(
    'POST', '/ai/suggested-questions', { profile, has_inventory: hasInventory, has_production: hasProduction }
  )
