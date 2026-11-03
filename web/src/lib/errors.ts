import { ApiError } from '../api/client'

// Every request-level failure is an ApiError; anything else is a client-side
// fault whose text must never reach the user, so it maps to the safe fallback.
export function messageOf(error: unknown, fallback = '请求失败，请重试'): string {
  return error instanceof ApiError ? error.message : fallback
}

export function isConflict(error: unknown): boolean {
  return error instanceof ApiError && error.status === 409
}

export function needsConfiguration(error: unknown): boolean {
  return error instanceof ApiError && error.status === 503
}

// A retry is only safe when the server definitely ran nothing: 4xx except 409
// (a conflict is a decision the user must re-read) and 503 (configuration).
export function isUnknownOutcome(error: unknown): boolean {
  if (!(error instanceof ApiError)) return true
  return (error.status !== 503 && error.status >= 500) || error.status < 400
}
