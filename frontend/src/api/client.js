export class ApiError extends Error {
  constructor(message, status = 0, body = null) {
    super(message)
    this.status = status
    this.body = body
  }
}

export function errorMessage(body, fallback) {
  if (typeof body?.detail === 'string') return body.detail
  if (Array.isArray(body?.detail))
    return body.detail
      .map((e) => `${(e.loc || []).filter((v) => v !== 'body').join('.')}: ${e.msg}`)
      .join('；')
  return fallback
}

// 不自动重试写请求；超时不能作为后端操作未执行的证据。
export function createClient({ base, getToken, onUnauthorized, fetchImpl = globalThis.fetch }) {
  const origin = new URL(base)
  if (
    !['http:', 'https:'].includes(origin.protocol) ||
    origin.username ||
    origin.password ||
    origin.search ||
    origin.hash
  )
    throw new Error('API 地址需为无凭据的 HTTP(S) 地址')
  const root = base.replace(/\/$/, '')
  return async (
    path,
    { method = 'GET', data, blob = false, public: isPublic = false, signal, timeout = 30000 } = {},
  ) => {
    if (!path.startsWith('/') || path.startsWith('//')) throw new Error('无效的 API 路径')
    const controller = new AbortController()
    const timer = setTimeout(() => controller.abort(), timeout)
    const cancel = () => controller.abort()
    signal?.addEventListener('abort', cancel, { once: true })
    if (signal?.aborted) controller.abort()
    try {
      const headers = { Accept: blob ? 'image/png, application/octet-stream' : 'application/json' }
      const token = getToken()
      if (!isPublic && token) headers.Authorization = `Bearer ${token}`
      const form = typeof FormData !== 'undefined' && data instanceof FormData
      if (data !== undefined && !form) headers['Content-Type'] = 'application/json'
      const response = await fetchImpl(root + path, {
        method,
        headers,
        body: data === undefined ? undefined : form ? data : JSON.stringify(data),
        signal: controller.signal,
        credentials: 'omit',
        cache: 'no-store',
        redirect: 'error',
      })
      if (response.status === 204) return null
      if (!response.ok) {
        let body
        try {
          body = await response.json()
        } catch {
          body = null
        }
        if (response.status === 401 && !isPublic) onUnauthorized?.(token)
        throw new ApiError(
          errorMessage(body, `请求失败（${response.status}）`),
          response.status,
          body,
        )
      }
      return blob ? await response.blob() : await response.json()
    } catch (error) {
      if (error instanceof ApiError) throw error
      if (controller.signal.aborted)
        throw new ApiError('请求已中断或超时。操作可能仍在后端执行，请刷新状态后再决定下一步。')
      throw new ApiError('无法连接后端，请检查本地服务和网络。已编辑的内容会保留。')
    } finally {
      clearTimeout(timer)
      signal?.removeEventListener('abort', cancel)
    }
  }
}
