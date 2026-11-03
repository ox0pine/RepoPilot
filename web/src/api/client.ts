import { clearSession, sessionToken, sessionVersion } from '../stores/session'

let unauthorizedHandler: (() => void) | undefined

export function onUnauthorized(handler: () => void): void {
  unauthorizedHandler = handler
}

export class ApiError extends Error {
  constructor(
    message: string,
    public readonly status: number,
    public readonly fields: Record<string, string> = {},
  ) {
    super(message)
    this.name = 'ApiError'
  }
}

// 422 bodies carry a per-field list; a request must be able to fill the form back.
function detailOf(payload: unknown): { message: string; fields: Record<string, string> } {
  if (typeof payload === 'object' && payload !== null && 'detail' in payload) {
    const detail = payload.detail
    if (typeof detail === 'string' && detail) return { message: detail, fields: {} }
    if (Array.isArray(detail)) {
      const fields: Record<string, string> = {}
      let message = ''
      for (const entry of detail as Array<{ loc?: unknown; msg?: unknown }>) {
        if (typeof entry?.msg !== 'string' || !entry.msg) continue
        message ||= entry.msg
        const loc = Array.isArray(entry.loc) ? entry.loc.filter(part => part !== 'body') : []
        const key = loc.length ? String(loc[loc.length - 1]) : ''
        if (key && !fields[key]) fields[key] = entry.msg
      }
      return { message: message || '请求失败', fields }
    }
  }
  return { message: '请求失败', fields: {} }
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
    const { message, fields } = detailOf(payload)
    throw new ApiError(message, response.status, fields)
  }
  if (payload === null) throw new ApiError('暂时无法读取结果，请刷新后重试', response.status)
  return payload as T
}
