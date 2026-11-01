import { reactive, watch } from 'vue'
import { getTaskStats, listTasks, type TaskStats, type TaskStatus, type TaskSummary } from '../api/tasks'
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
  filter: null as TaskStatus | null,
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

let pollTimer: ReturnType<typeof setTimeout> | undefined
const POLL_INTERVAL = 3000

function stopPoll(): void {
  clearTimeout(pollTimer)
  pollTimer = undefined
}

// Only a list that is actually waiting on the model needs to move by itself.
function schedulePoll(): void {
  stopPoll()
  if (!sessionToken.value || !taskStore.items.some(item => item.status === 'generating')) return
  const version = sessionVersion()
  const sequence = refreshSequence
  pollTimer = setTimeout(() => {
    pollTimer = undefined
    if (version === sessionVersion() && sequence === refreshSequence) void pollTaskState()
  }, POLL_INTERVAL)
}

// Merges the first page in place: no reordering, no paging, and it never aborts
// another request, so a poll cannot clobber a manual refresh that is in flight.
async function pollTaskState(): Promise<void> {
  const version = sessionVersion()
  const sequence = refreshSequence
  const listController = trackRequest()
  const statsController = trackRequest()
  await Promise.all([
    (async () => {
      try {
        const result = await listTasks(10, null, listController.signal, taskStore.filter)
        if (!isCurrent(version, sequence, listController)) return
        for (const item of result.items) {
          const existing = taskStore.items.find(entry => entry.id === item.id)
          if (existing && item.revision >= existing.revision) Object.assign(existing, item)
        }
        if (taskStore.filter !== null && taskStore.nextCursor === null) {
          const matching = new Set(result.items.map(item => item.id))
          taskStore.items = taskStore.items.filter(item => matching.has(item.id))
        }
      } catch {
        // A poll is best effort; the refresh button reports real errors.
      } finally {
        controllers.delete(listController)
      }
    })(),
    (async () => {
      try {
        const result = await getTaskStats(statsController.signal)
        if (isCurrent(version, sequence, statsController)) taskStore.stats = result
      } catch {
        // Ignore; the next poll or a manual refresh will pick it up.
      } finally {
        controllers.delete(statsController)
      }
    })(),
  ])
  if (version === sessionVersion() && sequence === refreshSequence) schedulePoll()
}

export function resetTasks(): void {
  refreshSequence += 1
  stopPoll()
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
  taskStore.filter = null
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
  stopPoll()
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
        const result = await listTasks(10, null, listController.signal, taskStore.filter)
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
  schedulePoll()
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
    const result = await listTasks(10, cursor, controller.signal, taskStore.filter)
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

export function setTaskFilter(status: TaskStatus | null): void {
  if (taskStore.filter === status) return
  taskStore.filter = status
  void refreshTasks()
}

export function setAutoGenerate(id: string): void {
  if (sessionToken.value) autoGenerate.add(id)
}

export function consumeAutoGenerate(id: string): boolean {
  return autoGenerate.delete(id)
}
