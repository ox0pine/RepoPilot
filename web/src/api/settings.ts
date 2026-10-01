import { request } from './client'

export type ModelSettings = {
  base_url: string
  model: string
  api_key_configured: boolean
}

export function getSettings(): Promise<ModelSettings> {
  return request<ModelSettings>('/api/settings')
}

export function updateModelSettings(payload: { base_url: string; api_key?: string; model: string }): Promise<ModelSettings> {
  return request<ModelSettings>('/api/settings/model', { method: 'PUT', body: JSON.stringify(payload) })
}

export function refreshModels(payload: { base_url: string; api_key?: string }): Promise<{ models: string[] }> {
  return request<{ models: string[] }>('/api/models/refresh', { method: 'POST', body: JSON.stringify(payload) })
}
