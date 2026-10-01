import { request } from './client'

export function authenticate(token: string): Promise<{ authenticated: true }> {
  return request<{ authenticated: true }>('/api/auth', {
    method: 'POST',
    headers: { Authorization: `Bearer ${token}` },
  })
}
