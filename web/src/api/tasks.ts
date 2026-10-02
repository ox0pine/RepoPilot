import { request } from './client'

export type TaskStatus = 'draft' | 'generating' | 'awaiting_approval' | 'approved' | 'generation_failed'
export type GenerationAction = 'generate' | 'revise' | 'retry'

export interface CreateTask {
  repository_url: string
  baseline_commit: string
  issue_url: string
}

export interface GoalContent {
  summary: string
  scope: string[]
  non_goals: string[]
  acceptance_criteria: string[]
  plan: string[]
  open_questions: string[]
}

export interface ContextFile {
  path: string
  blob_sha: string
  content: string
  truncated: boolean
  reason: string
}

export interface RepositoryContext {
  commit: string
  tree: string[]
  files: ContextFile[]
  omissions: string[]
  tree_truncated: boolean
}

export interface TaskGoal {
  version: number
  content: GoalContent
  model: string
  created_at: string
}

export interface TaskMessage {
  id: number
  kind: 'source' | 'feedback' | 'goal' | 'approval'
  text: string
  goal_version: number | null
  created_at: string
}

export interface TaskSummary extends CreateTask {
  id: string
  title: string
  status: TaskStatus
  revision: number
  current_goal_version: number
  approved_goal_version: number | null
  created_at: string
  updated_at: string
}

export interface TaskDetail extends TaskSummary {
  source_snapshot: CreateTask & {
    issue_number: number
    issue_title: string
    issue_body: string
    issue_updated_at: string
    fetched_at: string
    repository_context: RepositoryContext
  }
  goals: TaskGoal[]
  messages: TaskMessage[]
  approved_at: string | null
  last_error: string | null
  generation_deadline: string | null
}

export interface TaskList {
  items: TaskSummary[]
  next_cursor: string | null
}

export interface TaskStats {
  total: number
  draft: number
  generating: number
  awaiting_approval: number
  approved: number
  generation_failed: number
}

export function listTasks(limit: number, cursor: string | null, signal: AbortSignal): Promise<TaskList> {
  const query = new URLSearchParams({ limit: String(limit) })
  if (cursor !== null) query.set('cursor', cursor)
  return request<TaskList>(`/api/tasks?${query}`, { signal })
}

export function getTask(id: string, signal: AbortSignal): Promise<TaskDetail> {
  return request<TaskDetail>(`/api/tasks/${encodeURIComponent(id)}`, { signal })
}

export function getTaskStats(signal: AbortSignal): Promise<TaskStats> {
  return request<TaskStats>('/api/tasks/stats', { signal })
}

export function createTask(payload: CreateTask, signal: AbortSignal): Promise<TaskDetail> {
  return request<TaskDetail>('/api/tasks', { method: 'POST', body: JSON.stringify(payload), signal })
}

export function generateGoal(id: string, payload: { expected_revision: number; action: GenerationAction; feedback?: string }, signal: AbortSignal): Promise<TaskDetail> {
  return request<TaskDetail>(`/api/tasks/${encodeURIComponent(id)}/goal`, { method: 'POST', body: JSON.stringify(payload), signal })
}

export function approveGoal(id: string, payload: { expected_revision: number; goal_version: number }, signal: AbortSignal): Promise<TaskDetail> {
  return request<TaskDetail>(`/api/tasks/${encodeURIComponent(id)}/approve`, { method: 'POST', body: JSON.stringify(payload), signal })
}
