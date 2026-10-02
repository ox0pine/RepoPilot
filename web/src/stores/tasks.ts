import { reactive, watch } from 'vue'
import { getTaskStats, listTasks, type TaskStats, type TaskSummary } from '../api/tasks'
import { sessionToken, sessionVersion } from './session'

export const taskStore = reactive({
  items: [] as TaskSummary[],
  nextCursor: null as string | null,
  listLoading: false,
  listError: null as string | null,
  stats: null as TaskStats | null,
  statsLoading: false,
  statsError: null as string | null,
  moreLoading: false,
})

const controllers = new Set<AbortController>()
const autoGenerate = new Set<string>()
let refreshSequence = 0

function abortRequests(): void {
  for (const controller of controllers) controller.abort()
  controllers.clear()
}

function trackRequest(): AbortController {
  const controller = new AbortController()
  controllers.add(controller)
  return controller
}

function isCurrent(version: number, sequence: number, controller: AbortController): boolean {
  return version === sessionVersion() && sequence === refreshSequence && !controller.signal.aborted
}

function errorMessage(error: unknown): string {
  return error instanceof Error ? error.message : '请求失败，请重试'
}

export function resetTasks(): void {
  refreshSequence += 1
  abortRequests()
  autoGenerate.clear()
  taskStore.items = []
  taskStore.nextCursor = null
  taskStore.listLoading = false
  taskStore.listError = null
  taskStore.stats = null
  taskStore.statsLoading = false
  taskStore.statsError = null
  taskStore.moreLoading = false
}

// The version, rather than token text, distinguishes A → B → A and re-login.
watch(() => sessionVersion(), resetTasks, { flush: 'sync' })

export async function refreshTasks(): Promise<void> {
  if (!sessionToken.value) {
    resetTasks()
    return
  }
  const version = sessionVersion()
  const sequence = ++refreshSequence
  abortRequests()
  taskStore.listLoading = true
  taskStore.statsLoading = true
  taskStore.moreLoading = false
  taskStore.listError = null
  taskStore.statsError = null
  taskStore.nextCursor = null
  const listController = trackRequest()
  const statsController = trackRequest()

  await Promise.all([
    (async () => {
      try {
        const result = await listTasks(10, null, listController.signal)
        if (!isCurrent(version, sequence, listController)) return
        taskStore.items = result.items
        taskStore.nextCursor = result.next_cursor
      } catch (error) {
        if (isCurrent(version, sequence, listController)) taskStore.listError = errorMessage(error)
      } finally {
        controllers.delete(listController)
        if (isCurrent(version, sequence, listController)) taskStore.listLoading = false
      }
    })(),
    (async () => {
      try {
        const result = await getTaskStats(statsController.signal)
        if (isCurrent(version, sequence, statsController)) taskStore.stats = result
      } catch (error) {
        if (isCurrent(version, sequence, statsController)) taskStore.statsError = errorMessage(error)
      } finally {
        controllers.delete(statsController)
        if (isCurrent(version, sequence, statsController)) taskStore.statsLoading = false
      }
    })(),
  ])
}

export async function loadMoreTasks(): Promise<void> {
  const cursor = taskStore.nextCursor
  if (!sessionToken.value || !cursor || taskStore.listLoading || taskStore.moreLoading) return
  const version = sessionVersion()
  const sequence = refreshSequence
  const controller = trackRequest()
  taskStore.moreLoading = true
  taskStore.listError = null
  try {
    const result = await listTasks(10, cursor, controller.signal)
    if (!isCurrent(version, sequence, controller) || taskStore.nextCursor !== cursor) return
    const ids = new Set(taskStore.items.map(item => item.id))
    for (const item of result.items) {
      if (!ids.has(item.id)) {
        taskStore.items.push(item)
        ids.add(item.id)
      }
    }
    taskStore.nextCursor = result.next_cursor
  } catch (error) {
    if (isCurrent(version, sequence, controller)) taskStore.listError = errorMessage(error)
  } finally {
    controllers.delete(controller)
    if (isCurrent(version, sequence, controller)) taskStore.moreLoading = false
  }
}

export function setAutoGenerate(id: string): void {
  if (sessionToken.value) autoGenerate.add(id)
}

export function consumeAutoGenerate(id: string): boolean {
  return autoGenerate.delete(id)
}
