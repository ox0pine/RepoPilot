import { request } from './client'

export type ModelSettings = {
  base_url: string
  model: string
  api_key_configured: boolean
}

export function getSettings(signal?: AbortSignal): Promise<ModelSettings> {
  return request<ModelSettings>('/api/settings', { signal })
}

export function updateModelSettings(payload: { base_url: string; api_key?: string; model: string }, signal?: AbortSignal): Promise<ModelSettings> {
  return request<ModelSettings>('/api/settings/model', { method: 'PUT', body: JSON.stringify(payload), signal })
}

export function refreshModels(payload: { base_url: string; api_key?: string }, signal?: AbortSignal): Promise<{ models: string[] }> {
  return request<{ models: string[] }>('/api/models/refresh', { method: 'POST', body: JSON.stringify(payload), signal })
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

export function getGitHubSettings(signal?: AbortSignal): Promise<GitHubSettings> {
  return request<GitHubSettings>('/api/settings/github', { signal })
}

export function updateGitHubSettings(payload: GitHubSettingsUpdate, signal?: AbortSignal): Promise<GitHubSettings> {
  return request<GitHubSettings>('/api/settings/github', { method: 'PUT', body: JSON.stringify(payload), signal })
}

export function authorizeGitHubSettings(signal?: AbortSignal): Promise<GitHubAuthorizationResult> {
  return request<GitHubAuthorizationResult>('/api/settings/github/authorize', { method: 'POST', signal })
}
