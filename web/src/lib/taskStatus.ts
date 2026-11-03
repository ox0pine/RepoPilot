import type { TaskStatus } from '../api/tasks'

export const TASK_STATUS_LABELS: Record<TaskStatus, string> = {
  draft: '待生成', generating: '生成中', awaiting_approval: '待批准',
  approved: '已批准', generation_failed: '生成失败',
}

export const TASK_STATUS_TONES: Record<TaskStatus, string> = {
  draft: 'muted', generating: 'accent', awaiting_approval: 'warning',
  approved: 'success', generation_failed: 'danger',
}

// Review order, not lifecycle order: what needs a decision comes first.
export const TASK_STATUS_ORDER: TaskStatus[] = [
  'awaiting_approval', 'generation_failed', 'generating', 'draft', 'approved',
]
