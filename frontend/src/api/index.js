import { createClient } from './client.js'

let tokenReader = () => ''
let expire = () => {}
export function configureAuth(getToken, onUnauthorized) {
  tokenReader = getToken
  expire = onUnauthorized
}
export const request = createClient({
  base: import.meta.env.VITE_API_BASE_URL || 'http://127.0.0.1:8000',
  getToken: () => tokenReader(),
  onUnauthorized: (token) => expire(token),
})
const query = (values) => {
  const params = new URLSearchParams(
    Object.entries(values || {}).filter(([, v]) => v !== undefined && v !== null && v !== ''),
  )
  return params.size ? `?${params}` : ''
}
const get = (path, values) => request(path + query(values))
const post = (path, data = {}, long = false) =>
  request(path, { method: 'POST', data, timeout: long ? 900000 : 30000 })
const put = (path, data) => request(path, { method: 'PUT', data })
const patch = (path, data) => request(path, { method: 'PATCH', data })
export const api = {
  platforms: () => get('/platforms'),
  resourceModels: {
    list: (kind) => get('/resource-models', { kind }),
    create: (data) => post('/resource-models', data),
    update: (id, data) => put(`/resource-models/${id}`, data),
    remove: (id) => request(`/resource-models/${id}`, { method: 'DELETE' }),
    test: (id) => post(`/resource-models/${id}/test`, {}, true),
  },
  models: {
    list: () => get('/analysis-models'),
    create: (data) => post('/analysis-models', data),
    update: (id, data) => put(`/analysis-models/${id}`, data),
    remove: (id) => request(`/analysis-models/${id}`, { method: 'DELETE' }),
    removeSystem: (id) =>
      request(`/analysis-models/system/${encodeURIComponent(id)}`, { method: 'DELETE' }),
    restoreSystem: () => post('/analysis-models/system/restore', {}),
    test: (id) => post(`/analysis-models/${id}/test`, {}, true),
  },
  auth: {
    login: (data) => request('/auth/login', { method: 'POST', data, public: true }),
    register: (data) => request('/auth/register', { method: 'POST', data, public: true }),
    me: () => get('/auth/me'),
    logout: () => post('/auth/logout'),
  },
  products: {
    list: (values) => get('/products', values),
    get: (id) => get(`/products/${id}`),
    create: (data) => post('/products', data),
    update: (id, data) => patch(`/products/${id}`, data),
    remove: (id) => request(`/products/${id}`, { method: 'DELETE' }),
    documents: (id, values) => get(`/products/${id}/documents`, values),
    upload: (id, file) => {
      const body = new FormData()
      body.append('file', file)
      return request(`/products/${id}/documents`, { method: 'POST', data: body, timeout: 120000 })
    },
    removeDocument: (id, doc) => request(`/products/${id}/documents/${doc}`, { method: 'DELETE' }),
    download: (id, doc) => request(`/products/${id}/documents/${doc}/download`, { blob: true }),
    index: (id, doc) => post(`/products/${id}/rag/documents/${doc}/index`, {}, true),
  },
  tasks: {
    list: (values) => get('/tasks', values),
    get: (id) => get(`/tasks/${id}`),
    create: (data) => post('/tasks', data),
    remove: (id) => request(`/tasks/${id}`, { method: 'DELETE' }),
    status: (id, status) => patch(`/tasks/${id}/status`, { status }),
  },
  workflow: {
    get: (id) => get(`/tasks/${id}/workflow`),
    start: (id, data) => post(`/tasks/${id}/workflow/start`, data, true),
    resume: (id, action, generation) =>
      post(`/tasks/${id}/workflow/resume`, generation ? { action, generation } : { action }, true),
    retry: (id) => post(`/tasks/${id}/workflow/retry`, {}, true),
  },
  content: {
    list: (values) => get('/contents', values),
    get: (id) => get(`/contents/${id}`),
    update: (id, data) => patch(`/contents/${id}`, data),
    image: (id, index) => request(`/contents/${id}/images/${index}`, { blob: true }),
    generateImages: (id, data) => post(`/contents/${id}/images/generate`, data, true),
    submitImages: (id) => post(`/contents/${id}/images/submit-review`, {}, true),
  },
  cases: {
    create: (data) => post('/rednote/cases', data),
    update: (id, data) => put(`/rednote/cases/${id}`, data),
    remove: (id) => request(`/rednote/cases/${id}`, { method: 'DELETE' }),
    analysis: (id) => get(`/rednote/analyses/${id}`),
    search: (values) => get('/rednote/cases/search', values),
    get: (id) => get(`/rednote/cases/${id}`),
    collect: (data) => post('/rednote/collect', data, true),
    analyze: (id, data) => post(`/rednote/cases/${id}/analyze`, data, true),
    analyses: (values) => get('/rednote/analyses', values),
    models: () => get('/rednote/analysis-models'),
    feedback: (id, content) => post(`/rednote/analyses/${id}/feedback`, { content }),
    revise: (id, data) => post(`/rednote/analyses/${id}/revise`, data, true),
    compare: (left, right) => get('/rednote/analyses/compare', { left_id: left, right_id: right }),
    browser: () => get('/rednote/browser-status'),
  },
  prompts: {
    list: (values) => get('/prompts', values),
    create: (data) => post('/prompts', data),
    update: (id, data) => put(`/prompts/${id}`, data),
    remove: (id) => request(`/prompts/${id}`, { method: 'DELETE' }),
  },
  rules: {
    remove: (id) => request(`/rules/custom/${id}`, { method: 'DELETE' }),
    updatePlatform: (id, data) => put(`/rules/platform/${id}`, data),
    removePlatform: (id) => request(`/rules/platform/${id}`, { method: 'DELETE' }),
    platform: (platform = 'rednote') =>
      get('/rules/platform', { platform_name: platform, limit: 100 }),
    custom: (platform = 'rednote') => get('/rules/custom', { platform_name: platform, limit: 100 }),
    create: (data) => post('/rules/custom', data),
    update: (id, data) => put(`/rules/custom/${id}`, data),
    checks: (id) => get('/rules/checks', { content_id: id, limit: 20 }),
    check: (id, force = false) => post('/rules/checks', { content_id: id, force }, true),
  },
  reviews: {
    create: (data) => post('/reviews', data),
    list: (id) => get('/reviews', { task_id: id, limit: 100 }),
  },
  accounts: {
    list: () => get('/platform-accounts', { limit: 100 }),
    current: () => get('/platform-accounts/browser-current'),
    create: (data) => post('/platform-accounts', data),
  },
  publishing: {
    list: (id) => get('/publish-records', { task_id: id, limit: 100 }),
    get: (id) => get(`/publish-records/${id}`),
    create: (data) => post('/publish-records', data),
    execute: (id) => post(`/publish-records/${id}/execute`, { confirm_account: true }, true),
    retry: (id, key) => post(`/publish-records/${id}/retry`, { idempotency_key: key }),
    result: (id, data) => post(`/publish-records/${id}/result`, data),
  },
  memory: {
    publications: (values) => get('/memory/publications', values),
    feedback: (id) => get(`/memory/feedback/${id}`),
    syncStatus: (id) => get(`/publish-records/${id}/memory/sync-status`),
    sync: (id) => post(`/publish-records/${id}/memory/sync`),
    publication: (id) => get(`/publish-records/${id}/memory/publication`),
    metrics: (id, values) =>
      get(`/publish-records/${id}/memory/metrics`, { limit: 100, ...values }),
    manual: (id, data) => post(`/publish-records/${id}/memory/metrics`, data),
    collect: (id, key, source = 'auto') =>
      post(`/publish-records/${id}/memory/collect`, { idempotency_key: key, source }, true),
    analyze: (id, data) => post(`/publish-records/${id}/memory/analyze`, data, true),
    experiences: (product, values) =>
      get('/memory/experiences', { product_id: product, limit: 100, ...values }),
    setActive: (id, is_active) => patch(`/memory/experiences/${id}`, { is_active }),
  },
}

export async function allPages(load, filters = {}) {
  const result = []
  for (let offset = 0; ; offset += 100) {
    const page = await load({ ...filters, offset, limit: 100 })
    result.push(...page)
    if (page.length < 100) return result
  }
}
