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

export type GitHubSettings = {
  public_key: string
  private_key_configured: boolean
  api_token_configured: boolean
}

export type GitHubSettingsUpdate = {
  api_token?: string | null
}

export type GitHubPublicKeyResult = {
  status: 'created' | 'already_exists'
  key_id: number
  public_key: string
}

export type GitHubAuthorizationResult = {
  settings: GitHubSettings
  registration: GitHubPublicKeyResult
}

export function getGitHubSettings(): Promise<GitHubSettings> {
  return request<GitHubSettings>('/api/settings/github')
}

export function updateGitHubSettings(payload: GitHubSettingsUpdate): Promise<GitHubAuthorizationResult> {
  return request<GitHubAuthorizationResult>('/api/settings/github', { method: 'PUT', body: JSON.stringify(payload) })
}
