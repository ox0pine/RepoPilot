import { request } from './client'

export type RunStatus = 'queued' | 'running' | 'completed' | 'failed' | 'blocked' | 'exhausted' | 'cancelled' | 'interrupted'
export interface CreateRunRequest {
  expected_revision: number
  goal_version: number
}
export interface RunSummary {
  id: string
  task_id: string
  goal_version: number
  status: RunStatus
  cancel_requested: boolean
  created_at: string
  started_at: string | null
  finished_at: string | null
  error: string | null
}
export interface RunCheck {
  phase: 'setup' | 'baseline' | 'final'
  command: string
  exit_code: number | null
  output: string
  truncated: boolean
}
export interface RunEvent {
  id: number
  kind: 'context' | 'assistant' | 'tool_start' | 'tool_result' | 'check' | 'artifact' | 'state'
  payload: Record<string, unknown>
  created_at: string
}
export interface RunDetail extends RunSummary {
  image: string
  image_id: string | null
  setup_command: string
  check_command: string
  report: string
  patch: string
  checks: RunCheck[]
  events: RunEvent[]
}
const runsPath = (taskId: string) => `/api/tasks/${encodeURIComponent(taskId)}/runs`
export function listRuns(taskId: string, signal: AbortSignal): Promise<RunSummary[]> {
  return request(runsPath(taskId), { signal })
}
export function getRun(taskId: string, runId: string, signal: AbortSignal): Promise<RunDetail> {
  return request(`${runsPath(taskId)}/${encodeURIComponent(runId)}`, { signal })
}
export function createRun(taskId: string, payload: CreateRunRequest, signal: AbortSignal): Promise<RunDetail> {
  return request(runsPath(taskId), { method: 'POST', body: JSON.stringify(payload), signal })
}
export function cancelRun(taskId: string, runId: string, signal: AbortSignal): Promise<RunDetail> {
  return request(`${runsPath(taskId)}/${encodeURIComponent(runId)}/cancel`, { method: 'POST', signal })
}
