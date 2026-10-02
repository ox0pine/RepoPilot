import { clearSession, sessionToken, sessionVersion } from '../stores/session'

let unauthorizedHandler: (() => void) | undefined

export function onUnauthorized(handler: () => void): void {
  unauthorizedHandler = handler
}

export class ApiError extends Error {
  constructor(message: string, public readonly status: number) {
    super(message)
    this.name = 'ApiError'
  }
}

export async function request<T>(path: string, init: RequestInit = {}): Promise<T> {
  const token = sessionToken.value
  const version = sessionVersion()
  const headers = new Headers(init.headers)
  headers.set('Accept', 'application/json')
  if (token && !headers.has('Authorization')) headers.set('Authorization', `Bearer ${token}`)
  if (init.body !== undefined) headers.set('Content-Type', 'application/json')
  const response = await fetch(path, { ...init, headers })
  const contentType = response.headers.get('content-type') || ''
  const payload: unknown = contentType.includes('application/json') ? await response.json().catch(() => null) : null
  if (response.status === 401 && token && version === sessionVersion()) {
    clearSession()
    unauthorizedHandler?.()
  }
  if (!response.ok) {
    const detail = typeof payload === 'object' && payload !== null && 'detail' in payload && typeof payload.detail === 'string' ? payload.detail : '请求失败'
    throw new ApiError(detail, response.status)
  }
  if (payload === null) throw new ApiError('服务端返回了无效响应', response.status)
  return payload as T
}
