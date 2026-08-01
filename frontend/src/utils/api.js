const BASE = '/api'
const TOKEN_KEY = 'fris-token'

async function uploadRequest(path, file) {
  const token = getToken()
  const formData = new FormData()
  formData.append('file', file)
  const res = await fetch(`${BASE}${path}`, {
    method: 'POST',
    headers: token ? { Authorization: `Bearer ${token}` } : {},
    body: formData
  })
  if (!res.ok) {
    const body = await res.json().catch(() => ({}))
    if (res.status === 401) setToken(null)
    throw new Error(body.detail || `Upload failed: ${res.status}`)
  }
  return res.json()
}

export function getToken() {
  return localStorage.getItem(TOKEN_KEY)
}

export function setToken(token) {
  if (token) localStorage.setItem(TOKEN_KEY, token)
  else localStorage.removeItem(TOKEN_KEY)
}

async function request(path, options = {}) {
  const token = getToken()
  const res = await fetch(`${BASE}${path}`, {
    headers: {
      'Content-Type': 'application/json',
      ...(token ? { Authorization: `Bearer ${token}` } : {})
    },
    ...options
  })
  if (!res.ok) {
    const body = await res.json().catch(() => ({}))
    if (res.status === 401) setToken(null)
    throw new Error(body.detail || `Request failed: ${res.status}`)
  }
  return res.json()
}

export const api = {
  // Auth
  signup: (data) => request('/auth/signup', { method: 'POST', body: JSON.stringify(data) }),
  login: (data) => request('/auth/login', { method: 'POST', body: JSON.stringify(data) }),
  me: () => request('/auth/me'),
  createApiKey: (name) => request('/auth/keys', { method: 'POST', body: JSON.stringify({ name }) }),
  listApiKeys: () => request('/auth/keys'),
  revokeApiKey: (id) => request(`/auth/keys/${id}`, { method: 'DELETE' }),

  // Team management
  listTeam: () => request('/auth/team'),
  inviteMember: (email, role) => request('/auth/invite', { method: 'POST', body: JSON.stringify({ email, role }) }),
  listInvites: () => request('/auth/invites'),
  revokeInvite: (id) => request(`/auth/invites/${id}`, { method: 'DELETE' }),
  acceptInvite: (data) => request('/auth/accept-invite', { method: 'POST', body: JSON.stringify(data) }),
  updateMemberRole: (accountId, role) => request(`/auth/team/${accountId}/role`, { method: 'PUT', body: JSON.stringify({ role }) }),
  removeMember: (accountId) => request(`/auth/team/${accountId}`, { method: 'DELETE' }),

  // Connector catalog
  listAvailableConnectors: () => request('/connectors/available'),

  // Connector instances
  listConnectors: () => request('/connectors'),
  createConnector: (data) => request('/connectors', {
    method: 'POST',
    body: JSON.stringify(data)
  }),
  getConnector: (id) => request(`/connectors/${id}`),
  deleteConnector: (id) => request(`/connectors/${id}`, { method: 'DELETE' }),

  // Lifecycle
  testConnector: (id) => request(`/connectors/${id}/test`, { method: 'POST' }),
  detectSchema: (id) => request(`/connectors/${id}/detect-schema`, { method: 'POST' }),
  confirmSchema: (id, data) => request(`/connectors/${id}/confirm-schema`, {
    method: 'POST',
    body: JSON.stringify(data)
  }),
  syncConnector: (id) => request(`/connectors/${id}/sync`, { method: 'POST' }),
  getSyncHistory: (id) => request(`/connectors/${id}/sync-history`),

  // Custom connector
  getCustomTemplate: () => request('/connectors/custom/template'),

  // Graph
  getGraphStats: () => request('/graph/stats'),
  queryGraph: (cypher, params) => request('/graph/query', {
    method: 'POST',
    body: JSON.stringify({ cypher, params })
  }),
  listEntities: (entityType, limit = 100) => {
    const q = entityType ? `?entity_type=${entityType}&limit=${limit}` : `?limit=${limit}`
    return request(`/graph/entities${q}`)
  },
  getEntity: (frisId) => request(`/graph/entities/${frisId}`),
  explainEntity: (frisId) => request(`/graph/entities/${frisId}/explain`),
  semanticSearch: (q, entityType, topK = 10) => {
    const params = new URLSearchParams({ q, top_k: topK })
    if (entityType) params.set('entity_type', entityType)
    return request(`/graph/search?${params.toString()}`)
  },
  backfillEmbeddings: (batchSize = 100) => request(`/graph/embeddings/backfill?batch_size=${batchSize}`, {
    method: 'POST'
  }),

  // Entity Resolution Review Queue
  getReviewQueue: (limit = 100, offset = 0) => request(`/graph/review-queue?limit=${limit}&offset=${offset}`),
  reviewEntity: (frisId, action) => request(`/graph/entities/${frisId}/review`, {
    method: 'POST',
    body: JSON.stringify({ action })
  }),

  // Graph Explorer
  browseNodes: ({ entityType, text, limit = 50, offset = 0 } = {}) => {
    const params = new URLSearchParams({ limit, offset })
    if (entityType) params.set('entity_type', entityType)
    if (text) params.set('text', text)
    return request(`/graph/nodes?${params.toString()}`)
  },
  getNeighbors: (frisId, depth = 1) => request(`/graph/neighbors/${frisId}?depth=${depth}`),
  getSubgraph: (frisId, depth = 2) => request(`/graph/subgraph?fris_id=${encodeURIComponent(frisId)}&depth=${depth}`),

  // NL Query (Phase 2)
  runQuery: (data) => request('/query', { method: 'POST', body: JSON.stringify(data) }),

  // Living context layer — canonical field definitions per entity type
  listContext: (entityType) => request(`/context${entityType ? `?entity_type=${entityType}` : ''}`),
  upsertContext: (entityType, propertyName, data) => request(`/context/${entityType}/${propertyName}`, {
    method: 'PUT',
    body: JSON.stringify(data)
  }),
  deleteContext: (entityType, propertyName) => request(`/context/${entityType}/${propertyName}`, { method: 'DELETE' }),

  // Uploads + Data Viewer
  uploadFile: (file) => uploadRequest('/uploads', file),
  getUploadPreview: (objectId) => request(`/uploads/${objectId}/preview`),

  // Documentation Center — live OpenAPI spec, so the API Reference tab
  // always reflects the real, currently-running endpoint set.
  getOpenApiSpec: () => request('/openapi.json'),

  // LLM Providers
  listProviders: () => request('/providers'),
  setApiKeyProvider: (provider, apiKey) => request('/providers/apikey', {
    method: 'POST',
    body: JSON.stringify({ provider, api_key: apiKey })
  }),
  setDefaultProvider: (provider) => request('/providers/default', {
    method: 'POST',
    body: JSON.stringify({ provider })
  }),
  deleteProvider: (provider) => request(`/providers/${provider}`, { method: 'DELETE' }),
  startOAuth: (provider, accountId) => `${BASE}/providers/${provider}/oauth/start?account_id=${accountId}`,

  // Billing
  getUsage: (filters = {}) => {
    const params = new URLSearchParams()
    if (filters.eventType) params.set('event_type', filters.eventType)
    if (filters.sinceDays) params.set('since_days', filters.sinceDays)
    params.set('limit', filters.limit || 200)
    params.set('offset', filters.offset || 0)
    return request(`/billing/usage?${params.toString()}`)
  },
  getBillingSummary: (sinceDays = 30) => request(`/billing/summary?since_days=${sinceDays}`),
  getInvoices: (months = 6) => request(`/billing/invoices?months=${months}`),
  getPricing: () => request('/billing/pricing'),

  // Logs
  getLogs: (filters = {}) => {
    const params = new URLSearchParams()
    if (filters.category) params.set('category', filters.category)
    if (filters.level) params.set('level', filters.level)
    if (filters.connectorId) params.set('connector_id', filters.connectorId)
    if (filters.search) params.set('search', filters.search)
    if (filters.sinceHours) params.set('since_hours', filters.sinceHours)
    params.set('limit', filters.limit || 200)
    params.set('offset', filters.offset || 0)
    return request(`/logs?${params.toString()}`)
  },
  getLogCounts: () => request('/logs/counts'),
  purgeLogs: (olderThanDays = 30) => request(`/logs/purge?older_than_days=${olderThanDays}`, {
    method: 'DELETE'
  })
}
